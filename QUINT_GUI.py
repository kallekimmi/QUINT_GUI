import argparse
import dearpygui.dearpygui as dpg
import threading
import queue
import time
import os
import sys

from pymodbus.client import ModbusTcpClient


# ============================================================================
# COMMAND LINE ARGUMENTS
# ============================================================================

parser = argparse.ArgumentParser(description="QUINT4 UPS Monitor")

parser.add_argument(
    "--ip",
    type=str,
    default=None,
    help="Target UPS IP address. If specified, the application auto-connects.",
)

parser.add_argument(
    "--port",
    type=int,
    default=502,
    help="Target Modbus TCP port.",
)

parser.add_argument(
    "--interval",
    type=float,
    default=2.0,
    help="Polling interval in seconds.",
)

parser.add_argument(
    "--points",
    type=int,
    default=3600,
    help="Maximum number of points stored for each plot.",
)

args = parser.parse_args()

# If an IP was supplied through the command line, the application will
# automatically attempt to connect after the GUI has been created.
auto_connect = args.ip is not None

# Default IP shown in the GUI when no command line IP was provided.
ip_address = args.ip if args.ip is not None else "192.168.19.23"


# ============================================================================
# UPS ALARM DEFINITIONS
# ============================================================================

# Bit definitions for the UPS warning register.
# The dictionary key is the bit number and the value is the human-readable
# description displayed in the GUI.
UPS_WARNINGS = {
    0: "Any counter exceeded",
    1: "Battery threshold V exceeded",
    2: "SOC [%] low exceeded",
    3: "SOC [min] low exceeded",
    4: "Battery mode active",
    5: "Capacity test active",
    6: "Shutdown triggered",
    7: "Service mode active",
}


# Bit definitions for the UPS alarm register.
UPS_ALARMS = {
    2: "Overload cutoff",
    5: "Internal fault",
    6: "Bat technologies differ",
    7: "Bat capacities differ",
    8: "Bat capacity test negative",
    9: "Bat capacity too high",
    10: "Bat capacitiy too low",
    11: "Temp drop between bat high",
    12: "Bat quality check negative",
    13: "Bat end of life reached",
    14: "No battery detected",
    16: "Replace battery",
}


# ============================================================================
# APPLICATION STATE
# ============================================================================

# Communication between the Modbus worker thread and the Dear PyGui thread
# is handled through this queue.
#
# Important:
# Dear PyGui widgets should normally only be modified from the main GUI
# thread. The worker thread therefore puts messages into this queue instead
# of directly modifying GUI widgets.
data_queue = queue.Queue()


# Historical values used by the telemetry plots.
# Format:
# {
#     "Input Voltage": [[timestamps], [values]],
#     ...
# }
plot_history = {}


# Maps a parameter name to its display unit.
# Example:
#     global_units["Input Voltage"] == "V"
global_units = {}


# Stores the latest value and animation state for each displayed parameter.
param_state = {}


# ---------------------------------------------------------------------------
# Thread synchronization
# ---------------------------------------------------------------------------

# Lock protecting connection state shared between the GUI thread and the
# Modbus worker thread.
connection_lock = threading.Lock()


# Event used to tell the worker thread that it should stop.
#
# threading.Event is preferable to a simple boolean because it provides
# thread-safe synchronization and also allows the worker to wait efficiently.
stop_event = threading.Event()


# Reference to the currently running polling thread.
poll_thread = None


# True while the application itself is running.
running = True


# True only after a Modbus connection has been successfully established.
#
# This value is intentionally NOT set to True when the user merely presses
# the Connect button. The connection must succeed first.
is_connected = False


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def resource_path(relative_path):
    """
    Return an absolute path to a bundled application resource.

    PyInstaller stores bundled resources in a temporary directory exposed
    through sys._MEIPASS. When running the Python script normally, the
    current working directory is used instead.
    """
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


def decode_hopping_alarm(hopping_val):
    """
    Decode the QUINT4 hopping alarm register into a readable string.

    The register contains several bit fields:
      - Bit 31      : alarm active flag
      - Bits 15-30  : device number
      - Bits 8-14   : alarm ID
      - Bits 5-7    : alarm type
      - Bits 0-4    : alarm number
    """
    # Bit 31 indicates whether a device alarm exists.
    if not ((hopping_val >> 31) & 0x01):
        return "No Device Alarms"

    dev = (hopping_val >> 15) & 0xFFFF
    aid = (hopping_val >> 8) & 0x7F
    typ = (hopping_val >> 5) & 0x07
    num = hopping_val & 0x1F

    # Convert the numeric device ID into a human-readable name.
    if 1 <= dev <= 5:
        device_name = f"Bat {dev}"
    elif dev == 6:
        device_name = "Charger"
    elif dev == 7:
        device_name = "Power Supply"
    else:
        device_name = f"Dev {dev}"

    # Convert the numeric alarm type into a readable name.
    if typ == 1:
        alarm_type = "Process"
    elif typ == 4:
        alarm_type = "Diagnosis"
    else:
        alarm_type = "Unknown"

    return f"[{num} Act] {alarm_type} | {device_name}: Code {aid}"


def set_connection_state(connected):
    """
    Update the application connection state.

    This function only changes the shared application state. GUI updates
    should be performed by the GUI thread.
    """
    global is_connected

    with connection_lock:
        is_connected = connected


def get_connection_state():
    """
    Return the current connection state in a thread-safe way.
    """
    with connection_lock:
        return is_connected


def set_status(message):
    """
    Queue a status message for the GUI thread.

    The worker thread must not directly modify Dear PyGui widgets.
    """
    data_queue.put({
        "status": "connection",
        "msg": message,
    })


def create_modbus_client(host, port):
    """
    Create a Modbus TCP client for the specified UPS.

    Keeping client creation in one function makes it easier to adapt the
    application if the Modbus configuration changes in the future.
    """
    return ModbusTcpClient(
        host=host,
        port=port,
    )


def read_ups_registers(client):
    """
    Read the UPS input registers.

    Pymodbus changed the name of the unit/slave argument between versions.
    We therefore try the modern/commonly used argument first and fall back
    to the alternative name if necessary.

    Returns:
        A Modbus response object or an exception.
    """
    try:
        return client.read_input_registers(
            address=0x2000,
            count=26,
            slave=1,
        )
    except TypeError:
        # Compatibility fallback for Pymodbus versions that use device_id.
        return client.read_input_registers(
            address=0x2000,
            count=26,
            device_id=1,
        )


def is_modbus_error(response):
    """
    Return True when a Modbus response represents an error.

    Pymodbus responses normally provide isError(), but exception objects can
    also reach this function. Handling both cases makes the polling loop more
    robust across Pymodbus versions.
    """
    if isinstance(response, Exception):
        return True

    return hasattr(response, "isError") and response.isError()


# ============================================================================
# MODBUS WORKER THREAD
# ============================================================================

def poll_ups_data(client, host, port):
    """
    Continuously poll the UPS until a disconnect is requested.

    This function runs in a background thread so that network communication
    never blocks the Dear PyGui interface.

    The client is connected once when the worker starts. It remains connected
    while polling and is closed when the worker exits.
    """
    global is_connected

    try:
        # --------------------------------------------------------------------
        # Establish the initial connection.
        # --------------------------------------------------------------------

        set_status(f"Connecting to {host}:{port}...")

        if not client.connect():
            # IMPORTANT:
            # We do NOT set is_connected=True here.
            #
            # This is the key fix for the original Connect/Disconnect bug:
            # the application is only considered connected after connect()
            # actually succeeds.
            set_connection_state(False)
            set_status(f"Unable to connect to {host}:{port}")
            return

        # The TCP connection succeeded.
        set_connection_state(True)
        set_status(f"Connected to {host}:{port}")

        # --------------------------------------------------------------------
        # Poll the UPS until the user disconnects or the application exits.
        # --------------------------------------------------------------------

        while not stop_event.is_set() and running:
            try:
                response = read_ups_registers(client)

                if is_modbus_error(response):
                    data_queue.put({
                        "status": "error",
                        "msg": f"Read Error: {response}",
                    })

                else:
                    regs = response.registers

                    # --------------------------------------------------------
                    # Decode the raw UPS registers.
                    # --------------------------------------------------------

                    is_battery_mode = (regs[0x00] & (1 << 4)) != 0
                    warning_register = regs[0x15]
                    alarm_register = (regs[0x17] << 16) | regs[0x16]
                    hopping_register = (regs[0x19] << 16) | regs[0x18]

                    # --------------------------------------------------------
                    # Convert raw registers into application data.
                    # --------------------------------------------------------

                    data = {
                        "Operating Status": "Battery Mode!" if is_battery_mode else "Mains Operation",
                        "Input Voltage": regs[0x04] / 1000.0,
                        "Input Current": regs[0x05] / 1000.0,
                        "Output Voltage": regs[0x06] / 1000.0,
                        "Output Current": regs[0x07] / 1000.0,
                        "Battery Voltage": regs[0x0A] / 1000.0,
                        "Charging Current": regs[0x0B] / 1000.0,
                        "Temperature": (regs[0x0D] - 273.15 if regs[0x0D] > 0 else 0),
                        "Remaining Time": (regs[0x11] << 16) + regs[0x10],
                        "Warnings": warning_register,
                        "Alarms": alarm_register,
                        "Hopping": hopping_register,
                        "Hopping Alarms": decode_hopping_alarm(hopping_register),
                        # Numeric representation used by the plot.
                        # 0 = mains, 1 = battery.
                        "Operating Mode (Plot)": 1.0 if is_battery_mode else 0.0,
                    }

                    # --------------------------------------------------------
                    # Decode individual warning and alarm bits.
                    # --------------------------------------------------------

                    alarms = {
                        f"W_{bit}": bool(warning_register & (1 << bit))
                        for bit in UPS_WARNINGS
                    }

                    alarms.update({
                        f"A_{bit}": bool(alarm_register & (1 << bit))
                        for bit in UPS_ALARMS
                    })

                    # Put the complete data packet in the GUI queue.
                    data_queue.put({
                        "status": "ok",
                        "data": data,
                        "alarms": alarms,
                    })

            except Exception as exc:
                # A read error should not kill the worker thread immediately.
                # We report it to the GUI and allow the next polling cycle
                # to retry.
                data_queue.put({
                    "status": "error",
                    "msg": f"Polling error: {exc}",
                })

            # Wait between polls.
            #
            # stop_event.wait() is better than time.sleep() here because
            # disconnecting can wake the worker immediately instead of
            # waiting for the entire polling interval.
            interval = max(
                0.1,
                float(dpg.get_value("ui_interval")),
            )

            stop_event.wait(interval)

    finally:
        # --------------------------------------------------------------------
        # Always close the client when the worker exits.
        # --------------------------------------------------------------------

        try:
            client.close()
        except Exception:
            pass

        set_connection_state(False)

        # Only report "Disconnected" if the application did not fail during
        # the initial connection attempt.
        if stop_event.is_set():
            set_status("Disconnected")


# ============================================================================
# CONNECTION CONTROL
# ============================================================================

def start_connection():
    """
    Start a connection attempt in a background thread.

    The GUI is temporarily disabled while the connection is being tested.
    The button will only change to "Disconnect" after the TCP connection
    succeeds.
    """
    global poll_thread

    # Ignore duplicate connection attempts.
    if get_connection_state():
        return

    host = dpg.get_value("ui_ip")
    port = dpg.get_value("ui_port")

    # Basic validation before starting the worker.
    if not host:
        dpg.set_value(
            "connection_status",
            "Connection failed: IP address is empty.",
        )
        return

    if port < 1 or port > 65535:
        dpg.set_value(
            "connection_status",
            "Connection failed: invalid port.",
        )
        return

    # Stop any previous worker before starting a new one.
    stop_event.clear()

    # Prevent editing connection parameters while an attempt is active.
    dpg.configure_item("ui_ip", enabled=False)
    dpg.configure_item("ui_port", enabled=False)
    dpg.configure_item("btn_connect", enabled=False)

    dpg.set_item_label("btn_connect", "Connecting...")

    # Create the Modbus client in the GUI thread, but perform the actual
    # network connection inside the worker thread.
    client = create_modbus_client(host, port)

    poll_thread = threading.Thread(
        target=poll_ups_data,
        args=(client, host, port),
        daemon=True,
    )

    poll_thread.start()


def disconnect():
    """
    Request a disconnect from the active polling thread.

    The worker will close the Modbus client in its finally block and report
    the final state back to the GUI through the queue.
    """
    if not get_connection_state():
        return

    set_status("Disconnecting...")

    # Signal the worker thread to stop.
    stop_event.set()

    # Mark the application as disconnected immediately so that a second
    # click cannot start another operation while shutdown is in progress.
    set_connection_state(False)


def toggle_connection():
    """
    Connect or disconnect depending on the current connection state.

    IMPORTANT:
    Connecting and disconnecting are deliberately handled separately.
    A failed connection attempt never changes the application into the
    connected state.
    """
    if get_connection_state():
        disconnect()
    else:
        start_connection()


def apply_connection_ui(connected):
    """
    Update connection-related GUI controls.

    This function must be called from the Dear PyGui/main thread.
    """
    if connected:
        dpg.set_item_label("btn_connect", "Disconnect")
        dpg.configure_item("btn_connect", enabled=True)
        dpg.configure_item("ui_ip", enabled=False)
        dpg.configure_item("ui_port", enabled=False)
    else:
        dpg.set_item_label("btn_connect", "Connect")
        dpg.configure_item("btn_connect", enabled=True)
        dpg.configure_item("ui_ip", enabled=True)
        dpg.configure_item("ui_port", enabled=True)


# ============================================================================
# DEAR PYGUI SETUP
# ============================================================================

dpg.create_context()


# ---------------------------------------------------------------------------
# Plot theme
# ---------------------------------------------------------------------------

with dpg.theme(tag="padding_theme"):
    with dpg.theme_component(dpg.mvPlot):
        dpg.add_theme_style(
            dpg.mvPlotStyleVar_FitPadding,
            0.05,
            0.1,
            category=dpg.mvThemeCat_Plots,
        )


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------

with dpg.window(
    tag="main_window",
    label="QUINT4 UPS Monitor",
):
    with dpg.group(horizontal=True):

        # ====================================================================
        # LEFT SIDE: Connection settings and UPS values
        # ====================================================================

        with dpg.child_window(width=380):

            dpg.add_text("CONNECTION SETTINGS")
            dpg.add_separator()

            with dpg.group(horizontal=True):
                dpg.add_text("IP:")
                dpg.add_input_text(
                    default_value=ip_address,
                    tag="ui_ip",
                    width=105,
                )

                dpg.add_text("Port:")
                dpg.add_input_int(
                    default_value=args.port,
                    tag="ui_port",
                    width=50,
                    step=0,
                )

                dpg.add_button(
                    label="Connect",
                    tag="btn_connect",
                    width=-1,
                    callback=toggle_connection,
                )

            # Connection status is intentionally separate from the button.
            # This makes connection failures visible without relying only
            # on the button label.
            dpg.add_text(
                "Not connected",
                tag="connection_status",
            )

            with dpg.group(horizontal=True):
                dpg.add_text("Poll interval(s):")
                dpg.add_input_float(
                    default_value=args.interval,
                    tag="ui_interval",
                    width=45,
                    step=0,
                )
                dpg.add_spacer(width=2)
                dpg.add_text("Sample Pts:")
                dpg.add_input_int(
                    default_value=args.points,
                    tag="ui_points",
                    width=55,
                    step=0,
                )

            dpg.add_spacer(height=10)
            dpg.add_text("QUINT4 UPS Data")
            dpg.add_separator()

            with dpg.table(
                header_row=True,
                borders_innerH=True,
                borders_innerV=True,
                resizable=False,
                policy=dpg.mvTable_SizingStretchProp,
            ):
                dpg.add_table_column(
                    label="Address",
                    init_width_or_weight=0.15,
                )
                dpg.add_table_column(
                    label="Variable",
                    init_width_or_weight=0.45,
                )
                dpg.add_table_column(
                    label="Value",
                    init_width_or_weight=0.40,
                )

                process_keys = [
                    ("0x2000", "Operating Status", "Str", ""),
                    ("0x2004", "Input Voltage", "Num", "V"),
                    ("0x2005", "Input Current", "Num", "A"),
                    ("0x2006", "Output Voltage", "Num", "V"),
                    ("0x2007", "Output Current", "Num", "A"),
                    ("0x200A", "Battery Voltage", "Num", "V"),
                    ("0x200B", "Charging Current", "Num", "A"),
                    ("0x200D", "Temperature", "Num", "°C"),
                    ("0x2010", "Remaining Time", "Num", ""),
                    ("0x2015", "Warnings", "Num", ""),
                    ("0x2016", "Alarms", "Num", ""),
                    ("0x2018", "Hopping", "Num", ""),
                    ("0x2018", "Hopping Alarms", "Str", ""),
                ]

                hidden_plots = [
                    "Operating Mode (Plot)"
                ]

                # Create one table row for every UPS parameter.
                for addr, name, dtype, unit in process_keys:

                    global_units[name] = unit

                    with dpg.table_row():
                        dpg.add_text(addr)
                        dpg.add_input_text(
                            default_value=name,
                            readonly=True,
                            width=-1,
                        )

                        # Each value has its own theme so its text color can
                        # be animated when values change.
                        with dpg.theme(tag=f"theme_{name}"):
                            with dpg.theme_component(dpg.mvInputText):
                                dpg.add_theme_color(
                                    dpg.mvThemeCol_FrameBg,
                                    [0, 0, 0, 0],
                                )
                                dpg.add_theme_color(
                                    dpg.mvThemeCol_Text,
                                    [255, 255, 255, 255],
                                    tag=f"color_{name}",
                                )

                        dpg.add_input_text(
                            default_value="-",
                            tag=f"val_{name}",
                            readonly=True,
                            width=-1,
                        )

                        dpg.bind_item_theme(
                            f"val_{name}",
                            f"theme_{name}",
                        )

                        # Numeric values are plotted.
                        if dtype == "Num":
                            plot_history[name] = [[], []]

                # The operating mode is a numeric value used only by the
                # graph and therefore does not need a table row.
                for hp in hidden_plots:
                    plot_history[hp] = [[], []]
                    global_units[hp] = ""

            dpg.add_spacer(height=10)
            dpg.add_text("Active Alarms (Bit Breakdowns)")
            dpg.add_separator()

            with dpg.table(
                header_row=False,
                borders_innerH=True,
                resizable=False,
                policy=dpg.mvTable_SizingStretchProp,
            ):
                dpg.add_table_column(init_width_or_weight=0.15)
                dpg.add_table_column(init_width_or_weight=0.60)
                dpg.add_table_column(init_width_or_weight=0.25)

                for bit, desc in list(UPS_WARNINGS.items()) + list(UPS_ALARMS.items()):
                    is_warning = desc in UPS_WARNINGS.values()
                    tag = f"W_{bit}" if is_warning else f"A_{bit}"
                    addr = "0x2015" if is_warning else "0x2016"

                    with dpg.table_row():
                        dpg.add_text(addr)
                        dpg.add_input_text(
                            default_value=f"Bit {bit}: {desc}",
                            readonly=True,
                            width=-1,
                        )

                        with dpg.theme(tag=f"theme_{tag}"):
                            with dpg.theme_component(dpg.mvInputText):
                                dpg.add_theme_color(
                                    dpg.mvThemeCol_FrameBg,
                                    [0, 0, 0, 0],
                                )
                                dpg.add_theme_color(
                                    dpg.mvThemeCol_Text,
                                    [150, 150, 150, 255],
                                    tag=f"color_{tag}",
                                )

                        dpg.add_input_text(
                            default_value="Inactive",
                            tag=f"val_{tag}",
                            readonly=True,
                            width=-1,
                        )

                        dpg.bind_item_theme(
                            f"val_{tag}",
                            f"theme_{tag}",
                        )

        # ====================================================================
        # RIGHT SIDE: Telemetry plots
        # ====================================================================

        with dpg.child_window(width=-1):

            dpg.add_text("Telemetry Plots")
            dpg.add_separator()

            with dpg.table(
                header_row=False,
                resizable=False,
                borders_innerV=True,
                policy=dpg.mvTable_SizingStretchSame,
            ):
                dpg.add_table_column()
                dpg.add_table_column()

                numeric_keys = list(plot_history.keys())

                for i in range(0, len(numeric_keys), 2):
                    with dpg.table_row():
                        for j in range(2):
                            if i + j >= len(numeric_keys):
                                continue

                            key = numeric_keys[i + j]

                            with dpg.group():
                                with dpg.theme(tag=f"theme_header_{key}"):
                                    with dpg.theme_component(dpg.mvText):
                                        dpg.add_theme_color(
                                            dpg.mvThemeCol_Text,
                                            [255, 255, 255, 255],
                                            tag=f"color_header_{key}",
                                        )

                                dpg.add_text(
                                    key,
                                    tag=f"header_{key}",
                                )

                                dpg.bind_item_theme(
                                    f"header_{key}",
                                    f"theme_header_{key}",
                                )

                                with dpg.plot(
                                    height=125,
                                    width=-1,
                                    tag=f"plot_{key}",
                                ):
                                    dpg.add_plot_axis(
                                        dpg.mvXAxis,
                                        tag=f"x_{key}",
                                    )
                                    dpg.add_plot_axis(
                                        dpg.mvYAxis,
                                        tag=f"y_{key}",
                                    )
                                    dpg.add_line_series(
                                        [],
                                        [],
                                        tag=f"series_{key}",
                                        parent=f"y_{key}",
                                    )


# ============================================================================
# VIEWPORT / APPLICATION STARTUP
# ============================================================================

dpg.create_viewport(
    title="Phoenix QUINT4 UPS Viewer",
    width=1300,
    height=1030,
)

# Load the application icon when available.
favicon_path = resource_path("favicon.ico")

if os.path.exists(favicon_path):
    try:
        dpg.set_viewport_small_icon(favicon_path)
        dpg.set_viewport_large_icon(favicon_path)
    except Exception as exc:
        print(f"Could not load application icon: {exc}")


dpg.setup_dearpygui()
dpg.show_viewport()

dpg.set_primary_window(
    "main_window",
    True,
)

dpg.bind_item_theme(
    "main_window",
    "padding_theme",
)

# Auto-connect after the GUI has been initialized.
if auto_connect:
    start_connection()


# ============================================================================
# MAIN GUI LOOP
# ============================================================================

start_time = time.time()

while dpg.is_dearpygui_running():

    # ------------------------------------------------------------------------
    # Process messages from the Modbus worker thread.
    #
    # All Dear PyGui modifications happen here in the main GUI thread.
    # ------------------------------------------------------------------------

    while not data_queue.empty():

        msg = data_queue.get_nowait()

        # ================================================================
        # Connection status message
        # ================================================================

        if msg["status"] == "connection":
            status_message = msg["msg"]

            dpg.set_value(
                "connection_status",
                status_message,
            )

            # The worker only reports "Connected" after connect() succeeds.
            if status_message.startswith("Connected"):
                apply_connection_ui(True)

            # A failed initial connection must restore the GUI to the
            # disconnected state. This is the important behavior missing
            # from the original implementation.
            elif status_message.startswith("Connection failed") or status_message.startswith("Unable to connect"):
                apply_connection_ui(False)

                # Ensure the button never gets stuck in "Disconnect".
                set_connection_state(False)

            elif status_message == "Disconnected":
                apply_connection_ui(False)

            continue

        # ================================================================
        # Modbus read error
        # ================================================================

        if msg["status"] == "error":
            dpg.set_value(
                "connection_status",
                msg["msg"],
            )
            continue

        # ================================================================
        # Successful UPS data update
        # ================================================================

        if msg["status"] != "ok":
            continue

        uptime = time.time() - start_time

        max_pts = max(
            10,
            dpg.get_value("ui_points"),
        )

        all_updates = msg["data"].copy()

        # Convert alarm booleans into the strings used by the UI.
        for key, value in msg["alarms"].items():
            all_updates[key] = "ACTIVE" if value else "Inactive"

        # ----------------------------------------------------------------
        # Update every displayed parameter.
        # ----------------------------------------------------------------

        for key, val in all_updates.items():

            unit_str = global_units.get(key, "")
            u_str = f" {unit_str}" if unit_str else ""

            # ------------------------------------------------------------
            # Format special values for display.
            # ------------------------------------------------------------

            if key in ["Warnings", "Alarms"]:
                raw_str = f"0x{int(val):04X}"
                table_display_val = raw_str
                plot_display_val = raw_str

            elif key == "Hopping":
                raw_str = f"0x{int(val):08X}"
                table_display_val = raw_str
                plot_display_val = raw_str

            elif key == "Remaining Time":
                minutes, seconds = divmod(int(val), 60)
                hours, minutes = divmod(minutes, 60)
                table_display_val = f"{hours}h {minutes}m {seconds}s"
                plot_display_val = f"{int(val)} s"

            else:
                raw_str = f"{val:.2f}" if isinstance(val, float) else str(val)
                table_display_val = f"{raw_str}{u_str}"
                plot_display_val = f"{raw_str}{u_str}"

            # ------------------------------------------------------------
            # Update the table value.
            # ------------------------------------------------------------

            value_tag = f"val_{key}"
            if dpg.does_item_exist(value_tag):
                dpg.set_value(
                    value_tag,
                    table_display_val,
                )

            # ------------------------------------------------------------
            # Update plot header.
            # ------------------------------------------------------------

            header_tag = f"header_{key}"
            if dpg.does_item_exist(header_tag):
                if key == "Operating Mode (Plot)":
                    header_text = f"Operating Mode (0=Mains, 1=Bat) = {int(val)}"
                else:
                    header_text = f"{key} = {plot_display_val}"

                dpg.set_value(
                    header_tag,
                    header_text,
                )

            # ------------------------------------------------------------
            # Detect value changes.
            #
            # The animation system uses this information to briefly
            # highlight parameters whenever their value changes.
            # ------------------------------------------------------------

            if key not in param_state or param_state[key]["val"] != val:
                if key.startswith("W_") or key.startswith("A_"):
                    transition_type = "to_active" if "ACTIVE" in str(val) else "to_inactive"
                else:
                    transition_type = "normal"

                param_state[key] = {
                    "val": val,
                    "time": time.time(),
                    "type": transition_type,
                }

            # ------------------------------------------------------------
            # Add numeric values to the plot history.
            # ------------------------------------------------------------

            if key in plot_history:
                plot_history[key][0].append(uptime)
                plot_history[key][1].append(float(val))

                # Keep only the configured number of points.
                if len(plot_history[key][0]) > max_pts:
                    plot_history[key][0] = plot_history[key][0][-max_pts:]
                    plot_history[key][1] = plot_history[key][1][-max_pts:]

                series_tag = f"series_{key}"
                if dpg.does_item_exist(series_tag):
                    dpg.set_value(
                        series_tag,
                        [
                            plot_history[key][0],
                            plot_history[key][1],
                        ],
                    )

                    # Do not automatically resize the graph while the
                    # user is hovering over it, as that would make manual
                    # inspection difficult.
                    if not dpg.is_item_hovered(f"plot_{key}"):
                        dpg.fit_axis_data(f"x_{key}")
                        dpg.fit_axis_data(f"y_{key}")

    # ========================================================================
    # Update animated parameter colors.
    # ========================================================================

    current_time = time.time()

    for key, state in param_state.items():

        time_diff = current_time - state["time"]
        transition_type = state.get("type", "normal")

        if time_diff < 5.0:
            progress = time_diff / 5.0

            if transition_type == "to_active":
                r = 255
                g = int(100 - (100 - 50) * progress)
                b = int(100 - (100 - 50) * progress)
            elif transition_type == "to_inactive":
                r = int(50 + (150 - 50) * progress)
                g = int(255 - (255 - 150) * progress)
                b = int(50 + (150 - 50) * progress)
            else:
                r = int(50 + (255 - 50) * progress)
                g = int(220 + (255 - 220) * progress)
                b = int(220 + (255 - 220) * progress)

            current_color = [r, g, b, 255]

        else:
            if "ACTIVE" in str(state["val"]):
                current_color = [255, 50, 50, 255]
            elif "Inactive" in str(state["val"]):
                current_color = [150, 150, 150, 255]
            else:
                current_color = [255, 255, 255, 255]

        # Update the value color.
        value_color_tag = f"color_{key}"
        if dpg.does_item_exist(value_color_tag):
            dpg.set_value(
                value_color_tag,
                current_color,
            )

        # Update the corresponding plot header color.
        header_color_tag = f"color_header_{key}"
        if dpg.does_item_exist(header_color_tag):
            dpg.set_value(
                header_color_tag,
                current_color,
            )

    # Render one Dear PyGui frame.
    dpg.render_dearpygui_frame()


# ============================================================================
# APPLICATION SHUTDOWN
# ============================================================================

# Tell the Modbus worker to stop before destroying the GUI context.
running = False
stop_event.set()

# Give the worker a short opportunity to close the Modbus client cleanly.
if poll_thread is not None and poll_thread.is_alive():
    poll_thread.join(timeout=1.0)

dpg.destroy_context()
