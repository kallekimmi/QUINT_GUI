import argparse
import dearpygui.dearpygui as dpg
import threading
import queue
import time
from pymodbus.client import ModbusTcpClient

# -- COMMAND LINE ARGUMENTS --
parser = argparse.ArgumentParser(description="QUINT4 UPS Monitor")
parser.add_argument("--ip", type=str, default="127.0.0.1", help="Target UPS IP Address")
parser.add_argument("--port", type=int, default=5020, help="Target Modbus Port")
parser.add_argument("--interval", type=float, default=2.0, help="Update interval in seconds")
parser.add_argument("--points", type=int, default=3600, help="Max plot points to keep")
parser.add_argument("--auto", action="store_true", help="Auto-connect on launch")
args = parser.parse_args()

# -- STATIC ALARM BIT MAPS --
UPS_WARNINGS = {
    0: "Any counter exceeded", 1: "Battery threshold V exceeded",
    2: "SOC [%] low exceeded", 3: "SOC [min] low exceeded",
    4: "Battery mode active", 5: "Capacity test active",
    6: "Shutdown triggered", 7: "Service mode active"
}

UPS_ALARMS = {
    2: "Overload cutoff", 5: "Internal fault", 6: "Bat technologies differ",
    7: "Bat capacities differ", 8: "Bat capacity test negative",
    9: "Bat capacity too high", 10: "Bat capacitiy too low",
    11: "Temp drop between bat high", 12: "Bat quality check negative",
    13: "Bat end of life reached", 14: "No battery detected", 16: "Replace battery"
}

def decode_hopping_alarm(hopping_val):
    if not ((hopping_val >> 31) & 0x01): return "No Device Alarms"
    dev = (hopping_val >> 15) & 0xFFFF
    aid = (hopping_val >> 8) & 0x7F
    typ = (hopping_val >> 5) & 0x07
    num = hopping_val & 0x1F
    d_str = f"Bat {dev}" if 1 <= dev <= 5 else "Charger" if dev == 6 else "Power Supply" if dev == 7 else f"Dev {dev}"
    t_str = "Process" if typ == 1 else "Diagnosis" if typ == 4 else "Unknown"
    return f"[{num} Act] {t_str} | {d_str}: Code {aid}"

# Global State Variables
data_queue = queue.Queue()
plot_history = {} 
plot_titles = {} # Stores the formatted titles with units for the plots
param_state = {} 
is_connected = False
running = True

def poll_ups_data():
    client = ModbusTcpClient(dpg.get_value("ui_ip"), port=dpg.get_value("ui_port"))
    while is_connected and running:
        try:
            if client.connect():
                response = client.read_input_registers(address=0x2000, count=26, slave=1)
                if not response.isError():
                    regs = response.registers
                    is_battery_mode = (regs[0x00] & (1 << 4)) != 0
                    w_reg = regs[0x15]
                    a_reg = (regs[0x17] << 16) | regs[0x16]
                    hop_reg = (regs[0x19] << 16) | regs[0x18]

                    data = {
                        "Operating Status": ("Battery Mode!" if is_battery_mode else "Mains Operation"),
                        "Input Voltage": regs[0x04] / 1000.0,
                        "Input Current": regs[0x05] / 1000.0,
                        "Output Voltage": regs[0x06] / 1000.0,
                        "Output Current": regs[0x07] / 1000.0,
                        "Battery Voltage": regs[0x0A] / 1000.0,
                        "Charging Current": regs[0x0B] / 1000.0,
                        "Temperature": (regs[0x0D] - 273.15 if regs[0x0D] > 0 else 0),
                        "Remaining Time": (regs[0x11] << 16) + regs[0x10],
                        "Hopping Alarms": decode_hopping_alarm(hop_reg),
                        
                        "Operating Mode (Plot)": 1.0 if is_battery_mode else 0.0,
                        "Raw Warnings (Plot)": w_reg,
                        "Raw Alarms (Plot)": a_reg,
                        "Raw Hopping (Plot)": hop_reg
                    }
                    
                    alarms = {f"W_{b}": bool(w_reg & (1 << b)) for b in UPS_WARNINGS}
                    alarms.update({f"A_{b}": bool(a_reg & (1 << b)) for b in UPS_ALARMS})
                    data_queue.put({"status": "ok", "data": data, "alarms": alarms}) 
                else:
                    data_queue.put({"status": "error", "msg": "Read Error"}) 
            else:
                data_queue.put({"status": "error", "msg": "Connection Failed"}) 
        except Exception as e:
            data_queue.put({"status": "error", "msg": str(e)}) 
        
        time.sleep(dpg.get_value("ui_interval"))
    client.close()

def toggle_connection():
    global is_connected
    if is_connected:
        is_connected = False
        dpg.set_item_label("btn_connect", "Connect")
        dpg.configure_item("ui_ip", enabled=True)
        dpg.configure_item("ui_port", enabled=True)
    else:
        is_connected = True
        dpg.set_item_label("btn_connect", "Disconnect")
        dpg.configure_item("ui_ip", enabled=False)
        dpg.configure_item("ui_port", enabled=False)
        threading.Thread(target=poll_ups_data, daemon=True).start()

# --- Dear PyGui UI Setup ---
dpg.create_context() 

# ---------------------------------------------------------------------
# PLOT PADDING THEME IMPORT
# Creates a 5% margin on X-axis and 10% on Y-axis for all plots[cite: 3]
# ---------------------------------------------------------------------
with dpg.theme(tag="padding_theme"):
    with dpg.theme_component(dpg.mvPlot):
        dpg.add_theme_style(dpg.mvPlotStyleVar_FitPadding, 0.05, 0.1, category=dpg.mvThemeCat_Plots) #[cite: 3]

with dpg.window(tag="main_window", label="QUINT4 UPS Monitor"):
    with dpg.group(horizontal=True):
        
        # Left Side: Settings & Data Table
        with dpg.child_window(width=500):
            dpg.add_text("CONNECTION SETTINGS")
            dpg.add_separator()
            
            with dpg.group(horizontal=True):
                dpg.add_text("IP:")
                dpg.add_input_text(default_value=args.ip, tag="ui_ip", width=120)
                dpg.add_text("Port:")
                dpg.add_input_int(default_value=args.port, tag="ui_port", width=60, step=0)
                
            with dpg.group(horizontal=True):
                dpg.add_text("Rate(s):")
                dpg.add_input_float(default_value=args.interval, tag="ui_interval", width=55, step=0)
                dpg.add_text("Pts:")
                dpg.add_input_int(default_value=args.points, tag="ui_points", width=65, step=0)
                dpg.add_button(label="Connect", tag="btn_connect", width=-1, callback=toggle_connection)
                
            dpg.add_spacer(height=10)
            dpg.add_text("QUINT4 UPS Data")
            dpg.add_separator()
            
            with dpg.table(header_row=True, borders_innerH=True, borders_innerV=True, resizable=False, policy=dpg.mvTable_SizingStretchProp):
                dpg.add_table_column(label="Address", init_width_or_weight=0.15)
                dpg.add_table_column(label="Variable", init_width_or_weight=0.45)
                dpg.add_table_column(label="Value", init_width_or_weight=0.25) 
                dpg.add_table_column(label="Unit", init_width_or_weight=0.15)
                
                process_keys = [
                    ("0x2000", "Operating Status", "Str", ""),
                    ("0x2004", "Input Voltage", "Num", "V"),
                    ("0x2005", "Input Current", "Num", "A"),
                    ("0x2006", "Output Voltage", "Num", "V"),
                    ("0x2007", "Output Current", "Num", "A"),
                    ("0x200A", "Battery Voltage", "Num", "V"),
                    ("0x200B", "Charging Current", "Num", "A"),
                    ("0x200D", "Temperature", "Num", "°C"),
                    ("0x2010", "Remaining Time", "Num", "s"),
                    ("0x2018", "Hopping Alarms", "Str", "")
                ]
                
                hidden_plots = {
                    "Operating Mode (Plot)": "Operating Mode (0 = Mains Operation, 1 = Battery Operation)",
                    "Raw Warnings (Plot)": "Raw Warnings [Mask]", 
                    "Raw Alarms (Plot)": "Raw Alarms [Mask]", 
                    "Raw Hopping (Plot)": "Raw Hopping [Mask]"
                }
                
                for addr, name, dtype, unit in process_keys:
                    with dpg.table_row():
                        dpg.add_text(addr)
                        dpg.add_input_text(default_value=name, readonly=True, width=-1)
                        
                        with dpg.theme(tag=f"theme_{name}"):
                            with dpg.theme_component(dpg.mvInputText):
                                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, [0, 0, 0, 0])
                                dpg.add_theme_color(dpg.mvThemeCol_Text, [255, 255, 255, 255], tag=f"color_{name}")
                                
                        dpg.add_input_text(default_value="-", tag=f"val_{name}", readonly=True, width=-1)
                        dpg.bind_item_theme(f"val_{name}", f"theme_{name}")
                        dpg.add_text(unit)
                        
                        if dtype == "Num":
                            plot_history[name] = [[], []] 
                            plot_titles[name] = f"{name} [{unit}]" # Append unit to title
                
                for hp_key, hp_title in hidden_plots.items():
                    plot_history[hp_key] = [[], []]
                    plot_titles[hp_key] = hp_title

            dpg.add_spacer(height=10)
            dpg.add_text("Active Alarms (Bit Breakdowns)")
            dpg.add_separator()
            
            with dpg.table(header_row=False, borders_innerH=True, resizable=False, policy=dpg.mvTable_SizingStretchProp):
                dpg.add_table_column(init_width_or_weight=0.15)
                dpg.add_table_column(init_width_or_weight=0.65)
                dpg.add_table_column(init_width_or_weight=0.20)
                
                for bit, desc in list(UPS_WARNINGS.items()) + list(UPS_ALARMS.items()):
                    is_warning = desc in UPS_WARNINGS.values()
                    tag = f"W_{bit}" if is_warning else f"A_{bit}"
                    addr = "0x2015" if is_warning else "0x2016"
                    
                    with dpg.table_row():
                        dpg.add_text(addr)
                        dpg.add_input_text(default_value=f"Bit {bit}: {desc}", readonly=True, width=-1)
                        
                        with dpg.theme(tag=f"theme_{tag}"):
                            with dpg.theme_component(dpg.mvInputText):
                                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, [0, 0, 0, 0])
                                dpg.add_theme_color(dpg.mvThemeCol_Text, [150, 150, 150, 255], tag=f"color_{tag}")
                                
                        dpg.add_input_text(default_value="Inactive", tag=f"val_{tag}", readonly=True, width=-1)
                        dpg.bind_item_theme(f"val_{tag}", f"theme_{tag}")

        # Right Side: Dynamic Plots
        with dpg.child_window(width=-1):
            dpg.add_text("Telemetry Plots")
            dpg.add_separator()
            
            with dpg.table(header_row=False, resizable=False, borders_innerV=True, policy=dpg.mvTable_SizingStretchSame):
                dpg.add_table_column()
                dpg.add_table_column()
                
                numeric_keys = [k for k in plot_history.keys()]
                for i in range(0, len(numeric_keys), 2):
                    with dpg.table_row():
                        for j in range(2):
                            if i+j < len(numeric_keys):
                                key = numeric_keys[i+j]
                                with dpg.group():
                                    with dpg.theme(tag=f"theme_header_{key}"):
                                        with dpg.theme_component(dpg.mvText):
                                            dpg.add_theme_color(dpg.mvThemeCol_Text, [255, 255, 255, 255], tag=f"color_header_{key}")
                                    
                                    # Fetch the formatted title containing units or custom text
                                    display_title = plot_titles.get(key, key)
                                    dpg.add_text(display_title, tag=f"header_{key}")
                                    dpg.bind_item_theme(f"header_{key}", f"theme_header_{key}")
                                    
                                    with dpg.plot(height=125, width=-1, tag=f"plot_{key}"):
                                        dpg.add_plot_axis(dpg.mvXAxis, tag=f"x_{key}") 
                                        dpg.add_plot_axis(dpg.mvYAxis, tag=f"y_{key}") 
                                        dpg.add_line_series([], [], tag=f"series_{key}", parent=f"y_{key}") 

dpg.create_viewport(title='Phoenix QUINT4 UPS Viewer', width=1200, height=800) 
dpg.setup_dearpygui() 
dpg.show_viewport() 

# ---------------------------------------------------------------------
# BIND THE PADDING THEME
# Assigning it to the main window propagates it to all child plots[cite: 3]
# ---------------------------------------------------------------------
dpg.set_primary_window("main_window", True) 
dpg.bind_item_theme("main_window", "padding_theme") #[cite: 3]

if args.auto:
    toggle_connection()

start_time = time.time()

while dpg.is_dearpygui_running(): 
    max_pts = max(10, dpg.get_value("ui_points")) 
    
    while not data_queue.empty(): 
        msg = data_queue.get_nowait() 
        if msg["status"] == "ok":
            uptime = time.time() - start_time
            
            all_updates = msg["data"].copy()
            for k, v in msg["alarms"].items():
                all_updates[k] = "ACTIVE" if v else "Inactive"
            
            for key, val in all_updates.items():
                if dpg.does_item_exist(f"val_{key}"):
                    display_val = f"{val:.2f}" if isinstance(val, float) else str(val)
                    dpg.set_value(f"val_{key}", display_val)
                
                if key not in param_state or param_state[key]["val"] != val:
                    if key.startswith("W_") or key.startswith("A_"):
                        t_type = "to_active" if "ACTIVE" in str(val) else "to_inactive"
                    else:
                        t_type = "normal"
                        
                    param_state[key] = {"val": val, "time": time.time(), "type": t_type} 
                    
                if key in plot_history:
                    plot_history[key][0].append(uptime) 
                    plot_history[key][1].append(float(val)) 
                    
                    if len(plot_history[key][0]) > max_pts:
                        plot_history[key][0] = plot_history[key][0][-max_pts:]
                        plot_history[key][1] = plot_history[key][1][-max_pts:]
                        
                    if dpg.does_item_exist(f"series_{key}"):
                        dpg.set_value(f"series_{key}", [plot_history[key][0], plot_history[key][1]]) 
                        
                        # Stop auto-scaling if the user actively hovers over the plot with the mouse[cite: 3]
                        if not dpg.is_item_hovered(f"plot_{key}"):
                            dpg.fit_axis_data(f"x_{key}") 
                            dpg.fit_axis_data(f"y_{key}") 

    current_time = time.time()
    for key, state in param_state.items():
        time_diff = current_time - state["time"]
        t_type = state.get("type", "normal")
        
        if time_diff < 5.0:
            progress = time_diff / 5.0 
            
            if t_type == "to_active":
                r = 255
                g = int(100 - (100 - 50) * progress)
                b = int(100 - (100 - 50) * progress)
            elif t_type == "to_inactive":
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

        if dpg.does_item_exist(f"color_{key}"):
            dpg.set_value(f"color_{key}", current_color)
        if dpg.does_item_exist(f"color_header_{key}"):
            dpg.set_value(f"color_header_{key}", current_color)

    dpg.render_dearpygui_frame() 

running = False
dpg.destroy_context()