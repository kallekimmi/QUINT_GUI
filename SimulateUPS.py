"""
Phoenix Contact QUINT4 UPS - Modbus TCP Simulator Python 3.14.7 | PyModbus 3.16.1 | Dear PyGui 2.3.1

This application simulates a QUINT4 UPS and exposes measurements, status, warnings, and alarms through a Modbus TCP server.

Architecture:
- Dear PyGui provides the operator interface.
- sim_state stores the current UPS simulation state.
- The simulation engine updates measurements automatically.
- The Modbus device callback refreshes Input Registers on read requests.
- A background thread runs the Modbus TCP server.

Register addresses and scaling are based on the supplied simulation design. Verify them against the register documentation for the actual UPS model.
"""

import random
import threading
import time
import traceback

import dearpygui.dearpygui as dpg

from pymodbus.server import StartTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice

# =============================================================================
# 1. CONFIGURATION
# =============================================================================

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 502
UNIT_ID = 1

REGISTER_BASE = 0x2000
REGISTER_LAST = 0x2019
REGISTER_COUNT = REGISTER_LAST - REGISTER_BASE + 1

SIMULATION_INTERVAL = 0.5

# Protect shared state accessed by the GUI and Modbus server.
state_lock = threading.RLock()

# =============================================================================
# 2. WARNING AND ALARM DEFINITIONS
# =============================================================================

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

UPS_ALARMS = {
    2: "Overload cutoff",
    5: "Internal fault",
    6: "Battery technologies differ",
    7: "Battery capacities differ",
    8: "Battery capacity test negative",
    9: "Battery capacity too high",
    10: "Battery capacity too low",
    11: "Temperature drop between batteries high",
    12: "Battery quality check negative",
    13: "Battery end of life reached",
    14: "No battery detected",
    16: "Replace battery",
}

# =============================================================================
# 3. SHARED SIMULATION STATE
# =============================================================================
# Values ending in "_base" are nominal values used by automatic simulation.
# Values without "_base" are the current measurements exposed through Modbus.

sim_state = {
    "auto_mode": True,
    "tick": 0,
    "auto_state": "MAINS",

    # Nominal analog values
    "in_v_base": 24.1,
    "in_a_base": 4.5,
    "out_v_base": 24.0,
    "out_a_base": 4.5,
    "bat_v_base": 27.2,
    "chg_a_base": 0.02,
    "temp_base": 25.0,
    "time_base": 7200,

    # Current analog values
    "in_v": 24.1,
    "in_a": 4.5,
    "out_v": 24.0,
    "out_a": 4.5,
    "bat_v": 27.2,
    "chg_a": 0.02,
    "temp": 25.0,
    "time": 7200,

    # Digital status and bitmasks
    "status": 0,
    "warnings": 0,
    "alarms": 0,

    # Hopping alarm configuration
    "hop_active": False,
    "hop_dev": 0,
    "hop_id": 0,
    "hop_type": 0,
    "hop_num": 0,
}

# =============================================================================
# 4. MODBUS REGISTER MAP
# =============================================================================

def build_register_map():
    """ Create a snapshot of the current simulation state and convert it into a dictionary of absolute Modbus addresses and 16-bit values. """

    # Take a consistent snapshot of the shared state.
    with state_lock:
        s = sim_state.copy()

    # Construct the 32-bit hopping alarm value.
    hop_value = 0

    if s["hop_active"]:
        hop_value = (
            (1 << 31)
            | (int(s["hop_dev"]) << 15)
            | (int(s["hop_id"]) << 8)
            | (int(s["hop_type"]) << 5)
            | int(s["hop_num"])
        )

    # Convert temperature from Celsius to Kelvin.
    temperature_kelvin = int(s["temp"] + 273.15)

    # Convert runtime and alarms into non-negative integers.
    remaining_time = max(0, int(s["time"]))
    alarm_value = max(0, int(s["alarms"]))

    # Each Modbus register contains 16 bits.
    # The mask ensures that each value fits into an unsigned 16-bit register.
    return {
        0x2000: int(s["status"]) & 0xFFFF,

        0x2004: int(s["in_v"] * 1000) & 0xFFFF,
        0x2005: int(s["in_a"] * 1000) & 0xFFFF,
        0x2006: int(s["out_v"] * 1000) & 0xFFFF,
        0x2007: int(s["out_a"] * 1000) & 0xFFFF,

        0x200A: int(s["bat_v"] * 1000) & 0xFFFF,
        0x200B: int(s["chg_a"] * 1000) & 0xFFFF,

        0x200D: temperature_kelvin & 0xFFFF,
        0x200E: (temperature_kelvin + 5) & 0xFFFF,

        # Runtime: low 16-bit word followed by high 16-bit word.
        0x2010: remaining_time & 0xFFFF,
        0x2011: (remaining_time >> 16) & 0xFFFF,

        0x2015: int(s["warnings"]) & 0xFFFF,

        # Alarm bitmask: low word followed by high word.
        0x2016: alarm_value & 0xFFFF,
        0x2017: (alarm_value >> 16) & 0xFFFF,

        # Hopping alarm: low word followed by high word.
        0x2018: hop_value & 0xFFFF,
        0x2019: (hop_value >> 16) & 0xFFFF,
    }

# =============================================================================
# 5. PYMODBUS DEVICE CALLBACK
# =============================================================================

async def modbus_device_action(function_code, start_address, address, count, current_registers, set_values):
    """ Refresh the simulated Input Registers when a client reads them.

    start_address is the absolute address represented by
    current_registers[0]. Therefore, the correct list index is calculated
    by subtracting start_address from the absolute register address.
    """

    # This simulator dynamically serves Input Register reads (FC04).
    if function_code != 4 or set_values is not None:
        return None

    # Generate current values for this request.
    register_map = build_register_map()

    for offset in range(count):
        absolute_address = address + offset
        register_index = absolute_address - start_address

        # Check that the index is valid for the supplied register list.
        if not 0 <= register_index < len(current_registers):
            continue

        if REGISTER_BASE <= absolute_address <= REGISTER_LAST:
            # Reserved addresses return zero.
            current_registers[register_index] = register_map.get(
                absolute_address, 0
            )

    return None

# =============================================================================
# 6. MODBUS TCP SERVER
# =============================================================================

def create_modbus_device():
    """ Create the simulated device and its four Modbus data areas: coils, discrete inputs, holding registers, and input registers. """

    coils = SimData(
        address=0,
        count=1,
        values=False,
        datatype=DataType.BITS,
    )

    discrete_inputs = SimData(
        address=0,
        count=1,
        values=False,
        datatype=DataType.BITS,
    )

    holding_registers = SimData(
        address=0,
        count=1,
        values=0,
        datatype=DataType.REGISTERS,
    )

    # The Input Register block uses the actual absolute base address.
    input_registers = SimData(
        address=REGISTER_BASE,
        count=REGISTER_COUNT,
        values=[0] * REGISTER_COUNT,
        datatype=DataType.REGISTERS,
        readonly=True,
    )

    return SimDevice(
        id=UNIT_ID,
        simdata=(
            [coils],
            [discrete_inputs],
            [holding_registers],
            [input_registers],
        ),
        action=modbus_device_action,
    )


def modbus_server_thread():
    """Start the Modbus TCP server in its own background thread."""
    device = create_modbus_device()

    print(
        f"Starting QUINT4 simulator on "
        f"{SERVER_HOST}:{SERVER_PORT}, Unit ID {UNIT_ID}"
    )

    StartTcpServer(
        context=[device],
        address=(SERVER_HOST, SERVER_PORT),
    )

# =============================================================================
# 7. GUI HELPER FUNCTIONS
# =============================================================================

def set_gui_value(tag, value):
    """Update a GUI item if it exists."""
    if dpg.does_item_exist(tag):
        dpg.set_value(tag, value)


def clear_digital_controls():
    """Clear all status, warning, alarm, and hopping controls."""
    set_gui_value("ui_status_bat_mode", False)

    for bit in UPS_WARNINGS:
        set_gui_value(f"ui_w_{bit}", False)

    for bit in UPS_ALARMS:
        set_gui_value(f"ui_a_{bit}", False)

    set_gui_value("ui_hop_active", False)


def apply_scenario(scenario):
    """ Load a predefined manual scenario.

    NORMAL: normal mains operation.
    OUTAGE: input power failure and battery operation.
    BROKEN_BAT: simulated battery fault and associated alarms.
    """

    with state_lock:
        sim_state["auto_mode"] = False

    set_gui_value("ui_auto_mode", False)
    clear_digital_controls()

    if scenario == "NORMAL":
        analog_values = {
            "ui_in_v": 24.1,
            "ui_in_a": 4.5,
            "ui_out_v": 24.0,
            "ui_out_a": 4.5,
            "ui_bat_v": 27.2,
            "ui_chg_a": 0.02,
            "ui_temp": 25.0,
            "ui_time": 7200,
        }

    elif scenario == "OUTAGE":
        analog_values = {
            "ui_in_v": 0.0,
            "ui_in_a": 0.0,
            "ui_out_v": 23.8,
            "ui_out_a": 4.5,
            "ui_bat_v": 24.0,
            "ui_chg_a": 0.0,
            "ui_temp": 26.5,
            "ui_time": 180,
        }

        set_gui_value("ui_status_bat_mode", True)
        set_gui_value("ui_w_4", True)

        set_gui_value("ui_hop_active", True)
        set_gui_value("ui_hop_dev", 7)
        set_gui_value("ui_hop_type", 4)
        set_gui_value("ui_hop_id", 0)
        set_gui_value("ui_hop_num", 1)

    elif scenario == "BROKEN_BAT":
        analog_values = {
            "ui_in_v": 24.1,
            "ui_in_a": 4.5,
            "ui_out_v": 24.0,
            "ui_out_a": 4.5,
            "ui_bat_v": 18.2,
            "ui_chg_a": 0.0,
            "ui_temp": 45.0,
            "ui_time": 0,
        }

        set_gui_value("ui_w_1", True)
        set_gui_value("ui_a_16", True)
        set_gui_value("ui_a_5", True)
        set_gui_value("ui_a_8", True)

    else:
        raise ValueError(f"Unknown scenario: {scenario}")

    # Apply the analog preset to the GUI.
    for tag, value in analog_values.items():
        set_gui_value(tag, value)

    # Immediately synchronize GUI values with the shared state.
    with state_lock:
        read_analog_gui_to_state()
        read_digital_gui_to_state()

    print(f"Scenario loaded: {scenario}")

# =============================================================================
# 8. AUTOMATIC SIMULATION ENGINE
# =============================================================================

def process_auto_tick():
    """ Advance the automatic simulation by one step.

    The sequence is:
        MAINS -> OUTAGE -> RECOVERY -> MAINS

    Battery voltage, remaining runtime, and temperature change according
    to the current operating state. Random jitter is applied to measurements.
    """
    sim_state["tick"] += 1
    tick = sim_state["tick"]

    # -------------------------------------------------------------------------
    # MAINS: initialize normal operation
    # -------------------------------------------------------------------------

    if tick == 1:
        sim_state["auto_state"] = "MAINS"

        set_gui_value("ui_status_bat_mode", False)

        for bit in UPS_WARNINGS:
            set_gui_value(f"ui_w_{bit}", False)

        for bit in UPS_ALARMS:
            set_gui_value(f"ui_a_{bit}", False)

        set_gui_value("ui_hop_active", False)

        sim_state["in_v_base"] = 24.1
        sim_state["in_a_base"] = 4.5
        sim_state["out_v_base"] = 24.0
        sim_state["out_a_base"] = 4.5
        sim_state["bat_v_base"] = 27.2
        sim_state["chg_a_base"] = 0.02
        sim_state["time_base"] = 7200
        sim_state["temp_base"] = 25.0

    # -------------------------------------------------------------------------
    # OUTAGE: simulate mains failure
    # -------------------------------------------------------------------------

    elif tick == 60:
        sim_state["auto_state"] = "OUTAGE"

        set_gui_value("ui_status_bat_mode", True)
        set_gui_value("ui_w_4", True)

        set_gui_value("ui_hop_active", True)
        set_gui_value("ui_hop_dev", 7)
        set_gui_value("ui_hop_type", 4)
        set_gui_value("ui_hop_id", 0)
        set_gui_value("ui_hop_num", 1)

        sim_state["in_v_base"] = 0.0
        sim_state["in_a_base"] = 0.0
        sim_state["out_v_base"] = sim_state["bat_v_base"] - 0.2
        sim_state["out_a_base"] = 4.5
        sim_state["chg_a_base"] = 0.0
        sim_state["time_base"] = 300

    # -------------------------------------------------------------------------
    # RECOVERY: simulate restored mains power
    # -------------------------------------------------------------------------

    elif tick == 120:
        sim_state["auto_state"] = "RECOVERY"

        set_gui_value("ui_status_bat_mode", False)

        for bit in UPS_WARNINGS:
            set_gui_value(f"ui_w_{bit}", False)

        for bit in UPS_ALARMS:
            set_gui_value(f"ui_a_{bit}", False)

        set_gui_value("ui_hop_active", False)

        sim_state["in_v_base"] = 24.1
        sim_state["in_a_base"] = 6.5
        sim_state["out_v_base"] = 24.0
        sim_state["out_a_base"] = 4.5
        sim_state["chg_a_base"] = 2.0

    # Restart the cycle after 180 ticks.
    elif tick >= 180:
        sim_state["tick"] = 0
        return

    # -------------------------------------------------------------------------
    # Continuous changes during OUTAGE and RECOVERY
    # -------------------------------------------------------------------------

    state = sim_state["auto_state"]

    if state == "OUTAGE":
        sim_state["bat_v_base"] = max(
            0.0, sim_state["bat_v_base"] - 0.05
        )

        previous_time = sim_state["time_base"]

        sim_state["time_base"] = max(
            0, sim_state["time_base"] - 60
        )

        sim_state["temp_base"] += 0.05

        if sim_state["time_base"] == 0:
            sim_state["out_v_base"] = 0.0
            sim_state["out_a_base"] = 0.0

            if previous_time > 0:
                set_gui_value("ui_w_6", True)

    elif state == "RECOVERY":
        sim_state["bat_v_base"] = min(
            27.2, sim_state["bat_v_base"] + 0.1
        )

        sim_state["time_base"] = min(
            7200, sim_state["time_base"] + 60
        )

        sim_state["temp_base"] = max(
            25.0, sim_state["temp_base"] - 0.1
        )

    # -------------------------------------------------------------------------
    # Generate live analog measurements
    # -------------------------------------------------------------------------

    sim_state["in_v"] = (
        max(0.0, sim_state["in_v_base"] + random.uniform(-0.08, 0.08))
        if sim_state["in_v_base"] > 0 else 0.0
    )

    sim_state["in_a"] = (
        max(0.0, sim_state["in_a_base"] + random.uniform(-0.15, 0.15))
        if sim_state["in_a_base"] > 0 else 0.0
    )

    sim_state["out_v"] = (
        max(0.0, sim_state["out_v_base"] + random.uniform(-0.04, 0.04))
        if sim_state["out_v_base"] > 0 else 0.0
    )

    sim_state["out_a"] = (
        max(0.0, sim_state["out_a_base"] + random.uniform(-0.10, 0.10))
        if sim_state["out_a_base"] > 0 else 0.0
    )

    sim_state["bat_v"] = (
        max(0.0, sim_state["bat_v_base"] + random.uniform(-0.02, 0.02))
        if sim_state["bat_v_base"] > 0 else 0.0
    )

    sim_state["chg_a"] = (
        max(0.0, sim_state["chg_a_base"] + random.uniform(-0.05, 0.05))
        if sim_state["chg_a_base"] > 0 else 0.0
    )

    sim_state["temp"] = (
        sim_state["temp_base"] + random.uniform(-0.2, 0.2)
    )

    sim_state["time"] = sim_state["time_base"]

    # Refresh the GUI sliders unless the user is dragging them.
    gui_values = {
        "ui_in_v": sim_state["in_v"],
        "ui_in_a": sim_state["in_a"],
        "ui_out_v": sim_state["out_v"],
        "ui_out_a": sim_state["out_a"],
        "ui_bat_v": sim_state["bat_v"],
        "ui_chg_a": sim_state["chg_a"],
        "ui_temp": sim_state["temp"],
        "ui_time": sim_state["time"],
    }

    for tag, value in gui_values.items():
        if not dpg.is_item_active(tag):
            dpg.set_value(tag, value)

# =============================================================================
# 9. GUI-TO-STATE SYNCHRONIZATION
# =============================================================================

def read_digital_gui_to_state():
    """ Convert digital GUI controls into the status, warning, and alarm masks used by the Modbus register map. """

    # Status register: bit 4 represents battery mode.
    sim_state["status"] = (
        1 << 4
        if dpg.get_value("ui_status_bat_mode")
        else 0
    )

    # Warning register.
    warning_mask = 0

    for bit in UPS_WARNINGS:
        if dpg.get_value(f"ui_w_{bit}"):
            warning_mask |= 1 << bit

    sim_state["warnings"] = warning_mask

    # Alarm registers.
    alarm_mask = 0

    for bit in UPS_ALARMS:
        if dpg.get_value(f"ui_a_{bit}"):
            alarm_mask |= 1 << bit

    sim_state["alarms"] = alarm_mask

    # Hopping alarm configuration.
    sim_state["hop_active"] = dpg.get_value("ui_hop_active")
    sim_state["hop_dev"] = dpg.get_value("ui_hop_dev")
    sim_state["hop_type"] = dpg.get_value("ui_hop_type")
    sim_state["hop_id"] = dpg.get_value("ui_hop_id")
    sim_state["hop_num"] = dpg.get_value("ui_hop_num")


def read_analog_gui_to_state():
    """ Read analog sliders into the current and nominal simulation state.

    Manual mode uses these values directly without random jitter.
    """

    fields = {
        "in_v": "ui_in_v",
        "in_a": "ui_in_a",
        "out_v": "ui_out_v",
        "out_a": "ui_out_a",
        "bat_v": "ui_bat_v",
        "chg_a": "ui_chg_a",
        "temp": "ui_temp",
        "time": "ui_time",
    }

    for state_key, gui_tag in fields.items():
        value = dpg.get_value(gui_tag)

        sim_state[state_key] = value
        sim_state[f"{state_key}_base"] = value

# =============================================================================
# 10. DEAR PYGUI INTERFACE
# =============================================================================

def set_auto_mode(enabled):
    """Handle changes to the automatic simulation mode."""
    with state_lock:
        sim_state["auto_mode"] = bool(enabled)
        if enabled:
            sim_state["tick"] = 0


def build_gui():
    """Create the complete operator control panel."""
    dpg.create_context()

    with dpg.window(
        tag="main_window",
        label="UPS Simulator Control Panel",
    ):
        with dpg.group(horizontal=True):

            # Left panel: operating modes and presets.
            with dpg.child_window(width=200):
                dpg.add_text("SIMULATION MODE")
                dpg.add_separator()

                dpg.add_checkbox(
                    label="Auto Mode (Jitter)",
                    default_value=True,
                    tag="ui_auto_mode",
                    callback=lambda sender, value: set_auto_mode(value),
                )

                dpg.add_spacer(height=10)
                dpg.add_text("MANUAL PRESETS")
                dpg.add_separator()

                dpg.add_button(
                    label="Normal Mains",
                    width=-1,
                    callback=lambda: apply_scenario("NORMAL"),
                )

                dpg.add_spacer(height=2)

                dpg.add_button(
                    label="Outage / Disconnect",
                    width=-1,
                    callback=lambda: apply_scenario("OUTAGE"),
                )

                dpg.add_spacer(height=2)

                dpg.add_button(
                    label="Broken Battery",
                    width=-1,
                    callback=lambda: apply_scenario("BROKEN_BAT"),
                )

            # Middle panel: analog parameters.
            with dpg.child_window(width=400):
                dpg.add_text("ANALOG PARAMETERS")
                dpg.add_separator()

                dpg.add_slider_float(
                    label="In V",
                    tag="ui_in_v",
                    min_value=0.0,
                    max_value=30.0,
                    default_value=24.1,
                )

                dpg.add_slider_float(
                    label="In A",
                    tag="ui_in_a",
                    min_value=0.0,
                    max_value=60.0,
                    default_value=4.5,
                )

                dpg.add_slider_float(
                    label="Out V",
                    tag="ui_out_v",
                    min_value=0.0,
                    max_value=30.0,
                    default_value=24.0,
                )

                dpg.add_slider_float(
                    label="Out A",
                    tag="ui_out_a",
                    min_value=0.0,
                    max_value=60.0,
                    default_value=4.5,
                )

                dpg.add_slider_float(
                    label="Bat V",
                    tag="ui_bat_v",
                    min_value=0.0,
                    max_value=30.0,
                    default_value=27.2,
                )

                dpg.add_slider_float(
                    label="Chg A",
                    tag="ui_chg_a",
                    min_value=0.0,
                    max_value=60.0,
                    default_value=0.02,
                )

                dpg.add_slider_float(
                    label="Temp °C",
                    tag="ui_temp",
                    min_value=-40.0,
                    max_value=80.0,
                    default_value=25.0,
                )

                dpg.add_slider_int(
                    label="Rem. Time",
                    tag="ui_time",
                    min_value=0,
                    max_value=7200,
                    default_value=7200,
                )

            # Right panel: digital controls.
            with dpg.child_window(width=-1):
                dpg.add_text("DIGITAL BITS & ALARMS")
                dpg.add_separator()

                dpg.add_checkbox(
                    label="Status: Battery Mode Active",
                    tag="ui_status_bat_mode",
                )

                dpg.add_spacer(height=5)
                dpg.add_text("Warnings (0x2015)")

                for bit, description in UPS_WARNINGS.items():
                    dpg.add_checkbox(
                        label=f"Bit {bit}: {description}",
                        tag=f"ui_w_{bit}",
                    )

                dpg.add_spacer(height=5)
                dpg.add_text("Alarms (0x2016 / 0x2017)")

                for bit, description in UPS_ALARMS.items():
                    dpg.add_checkbox(
                        label=f"Bit {bit}: {description}",
                        tag=f"ui_a_{bit}",
                    )

                dpg.add_spacer(height=5)
                dpg.add_text("Hopping Alarm Config (0x2018 / 0x2019)")

                dpg.add_checkbox(
                    label="Active",
                    tag="ui_hop_active",
                )

                with dpg.group(horizontal=True):
                    dpg.add_input_int(
                        label="Dev",
                        tag="ui_hop_dev",
                        width=60,
                        step=0,
                    )

                    dpg.add_input_int(
                        label="Type",
                        tag="ui_hop_type",
                        width=60,
                        step=0,
                    )

                    dpg.add_input_int(
                        label="ID",
                        tag="ui_hop_id",
                        width=60,
                        step=0,
                    )

                    dpg.add_input_int(
                        label="Num",
                        tag="ui_hop_num",
                        width=60,
                        step=0,
                    )

    dpg.create_viewport(
        title="Phoenix QUINT4 Simulator Control",
        width=1050,
        height=650,
    )

    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window("main_window", True)

# =============================================================================
# 11. APPLICATION ENTRY POINT
# =============================================================================

def main():
    """Start the GUI, launch the Modbus server, and run the simulation loop."""
    build_gui()

    # Run the Modbus server independently of the GUI event loop.
    server_thread = threading.Thread(
        target=modbus_server_thread,
        name="QUINT4-Modbus-Server",
        daemon=True,
    )

    server_thread.start()

    last_tick = time.monotonic()

    try:
        while dpg.is_dearpygui_running():
            current_time = time.monotonic()

            # Synchronize GUI state and simulation values.
            with state_lock:
                read_digital_gui_to_state()

                if sim_state["auto_mode"]:
                    # Allow manual adjustment of nominal values while
                    # the operator is dragging sliders.
                    active_sliders = {
                        "ui_in_v": "in_v_base",
                        "ui_in_a": "in_a_base",
                        "ui_out_v": "out_v_base",
                        "ui_out_a": "out_a_base",
                        "ui_bat_v": "bat_v_base",
                        "ui_chg_a": "chg_a_base",
                        "ui_temp": "temp_base",
                        "ui_time": "time_base",
                    }

                    for gui_tag, state_key in active_sliders.items():
                        if dpg.is_item_active(gui_tag):
                            sim_state[state_key] = dpg.get_value(gui_tag)

                    # Advance the automatic simulation at a fixed interval.
                    if current_time - last_tick >= SIMULATION_INTERVAL:
                        process_auto_tick()
                        last_tick = current_time

                else:
                    # Manual mode uses GUI measurements directly.
                    read_analog_gui_to_state()

            # Render the next GUI frame.
            dpg.render_dearpygui_frame()

    except KeyboardInterrupt:
        print("Simulator interrupted by user.")

    except Exception:
        print("An unexpected application error occurred:")
        traceback.print_exc()

    finally:
        dpg.destroy_context()
        print("QUINT4 simulator stopped.")

if __name__ == "__main__":
    main()
