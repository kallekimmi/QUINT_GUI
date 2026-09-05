import threading
import time
import random
import dearpygui.dearpygui as dpg
from pymodbus.server import StartTcpServer
from pymodbus.datastore import ModbusSequentialDataBlock, ModbusSlaveContext, ModbusServerContext

# =====================================================================
# --- STATIC ALARM DICTIONARIES ---
# =====================================================================
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

# =====================================================================
# --- SHARED GLOBAL STATE ---
# =====================================================================
sim_state = {
    "auto_mode": True,
    "tick": 0,
    "auto_state": "MAINS",
    
    # Base values for jitter calculations (What the slider represents)
    "in_v_base": 24.1, "in_a_base": 4.5,
    "out_v_base": 24.0, "out_a_base": 4.5,
    "bat_v_base": 27.2, "chg_a_base": 0.02,
    "temp_base": 25.0,  "time_base": 7200,
    
    # Active Modbus Telemetry Values (Jittered values)
    "status": 0,
    "in_v": 24.1, "in_a": 4.5,
    "out_v": 24.0, "out_a": 4.5,
    "bat_v": 27.2, "chg_a": 0.02,
    "temp": 25.0, "time": 7200,
    "warnings": 0,
    "alarms": 0,
    
    # Hopping Alarm Components
    "hop_active": False,
    "hop_dev": 0, "hop_id": 0, "hop_type": 0, "hop_num": 0
}

# =====================================================================
# --- MODBUS SERVER THREADS ---
# =====================================================================
def modbus_server_thread(context):
    print("Starting Modbus TCP Simulator on 127.0.0.1:5020...")
    StartTcpServer(context=context, address=("127.0.0.1", 5020))

def update_modbus_registers(context):
    register_block = context[0].store['i']
    base_addr = 0x2001 
    
    while True:
        hop_reg = 0
        if sim_state["hop_active"]:
            hop_reg = (1 << 31) | (int(sim_state["hop_dev"]) << 15) | (int(sim_state["hop_id"]) << 8) | (int(sim_state["hop_type"]) << 5) | int(sim_state["hop_num"])
            
        register_block.setValues(base_addr + 0x00, [int(sim_state["status"])])
        register_block.setValues(base_addr + 0x04, [int(sim_state["in_v"] * 1000)])  
        register_block.setValues(base_addr + 0x05, [int(sim_state["in_a"] * 1000)])  
        register_block.setValues(base_addr + 0x06, [int(sim_state["out_v"] * 1000)]) 
        register_block.setValues(base_addr + 0x07, [int(sim_state["out_a"] * 1000)]) 
        register_block.setValues(base_addr + 0x0A, [int(sim_state["bat_v"] * 1000)]) 
        register_block.setValues(base_addr + 0x0B, [int(sim_state["chg_a"] * 1000)]) 
        
        temp_k = int(sim_state["temp"] + 273.15)
        register_block.setValues(base_addr + 0x0D, [temp_k])
        register_block.setValues(base_addr + 0x0E, [temp_k + 5])
        
        t_int = int(sim_state["time"])
        register_block.setValues(base_addr + 0x10, [t_int & 0xFFFF, (t_int >> 16) & 0xFFFF])
        
        register_block.setValues(base_addr + 0x15, [int(sim_state["warnings"])])
        a_int = int(sim_state["alarms"])
        register_block.setValues(base_addr + 0x16, [a_int & 0xFFFF, (a_int >> 16) & 0xFFFF])
        register_block.setValues(base_addr + 0x18, [hop_reg & 0xFFFF, (hop_reg >> 16) & 0xFFFF])

        time.sleep(0.5)

# =====================================================================
# --- SIMULATOR LOGIC & GUI SYNC ---
# =====================================================================
def apply_scenario(scenario):
    sim_state["auto_mode"] = False
    dpg.set_value("ui_auto_mode", False)
    
    dpg.set_value("ui_status_bat_mode", False)
    for b in UPS_WARNINGS: dpg.set_value(f"ui_w_{b}", False)
    for b in UPS_ALARMS: dpg.set_value(f"ui_a_{b}", False)
    dpg.set_value("ui_hop_active", False)
    
    if scenario == "NORMAL":
        dpg.set_value("ui_in_v", 24.1)
        dpg.set_value("ui_in_a", 4.5)
        dpg.set_value("ui_out_v", 24.0)
        dpg.set_value("ui_out_a", 4.5)
        dpg.set_value("ui_bat_v", 27.2)
        dpg.set_value("ui_chg_a", 0.02)
        dpg.set_value("ui_temp", 25.0)
        dpg.set_value("ui_time", 7200)
        
    elif scenario == "OUTAGE":
        dpg.set_value("ui_in_v", 0.0)
        dpg.set_value("ui_in_a", 0.0)
        dpg.set_value("ui_out_v", 23.8)
        dpg.set_value("ui_out_a", 4.5)
        dpg.set_value("ui_bat_v", 24.0)
        dpg.set_value("ui_chg_a", 0.0)
        dpg.set_value("ui_temp", 26.5)
        dpg.set_value("ui_time", 180) 
        
        dpg.set_value("ui_status_bat_mode", True)
        dpg.set_value("ui_w_4", True) 
        
        dpg.set_value("ui_hop_active", True)
        dpg.set_value("ui_hop_dev", 7)
        dpg.set_value("ui_hop_type", 4)
        dpg.set_value("ui_hop_id", 0)
        dpg.set_value("ui_hop_num", 1)
        
    elif scenario == "BROKEN_BAT":
        dpg.set_value("ui_in_v", 24.1)
        dpg.set_value("ui_in_a", 4.5)
        dpg.set_value("ui_out_v", 24.0)
        dpg.set_value("ui_out_a", 4.5)
        dpg.set_value("ui_bat_v", 18.2) 
        dpg.set_value("ui_chg_a", 0.0)
        dpg.set_value("ui_temp", 45.0)  
        dpg.set_value("ui_time", 0)
        
        dpg.set_value("ui_w_1", True)  
        dpg.set_value("ui_a_16", True) 
        dpg.set_value("ui_a_5", True)  
        dpg.set_value("ui_a_8", True)  

def process_auto_tick():
    sim_state["tick"] += 1
    t = sim_state["tick"]
    
    # --- EDGE TRIGGERED STATE CHANGES ---
    # Only overwrites checkboxes on the exact tick the phase shifts.
    if t == 1: 
        sim_state["auto_state"] = "MAINS"
        dpg.set_value("ui_status_bat_mode", False)
        for b in UPS_WARNINGS: dpg.set_value(f"ui_w_{b}", False)
        for b in UPS_ALARMS: dpg.set_value(f"ui_a_{b}", False)
        dpg.set_value("ui_hop_active", False)
        
        sim_state["in_v_base"] = 24.1
        sim_state["in_a_base"] = 4.5
        sim_state["out_v_base"] = 24.0
        sim_state["out_a_base"] = 4.5
        sim_state["chg_a_base"] = 0.02
        sim_state["bat_v_base"] = 27.2
        sim_state["time_base"] = 7200
        sim_state["temp_base"] = 25.0
        
    elif t == 60: 
        sim_state["auto_state"] = "OUTAGE"
        dpg.set_value("ui_status_bat_mode", True)
        dpg.set_value("ui_w_4", True) 
        dpg.set_value("ui_hop_active", True)
        dpg.set_value("ui_hop_dev", 7)
        dpg.set_value("ui_hop_type", 4)
        dpg.set_value("ui_hop_id", 0)
        dpg.set_value("ui_hop_num", 1)
        
        sim_state["in_v_base"] = 0.0
        sim_state["in_a_base"] = 0.0
        sim_state["out_v_base"] = sim_state["bat_v_base"] - 0.2
        sim_state["out_a_base"] = 4.5
        sim_state["chg_a_base"] = 0.0
        sim_state["time_base"] = 300 
        
    elif t == 120: 
        sim_state["auto_state"] = "RECOVERY"
        dpg.set_value("ui_status_bat_mode", False)
        for b in UPS_WARNINGS: dpg.set_value(f"ui_w_{b}", False)
        for b in UPS_ALARMS: dpg.set_value(f"ui_a_{b}", False)
        dpg.set_value("ui_hop_active", False)
        
        sim_state["in_v_base"] = 24.1
        sim_state["in_a_base"] = 6.5
        sim_state["out_v_base"] = 24.0
        sim_state["out_a_base"] = 4.5
        sim_state["chg_a_base"] = 2.0
        
    elif t >= 180: 
        sim_state["tick"] = 0

    # --- CONTINUOUS DRIFT ---
    state = sim_state["auto_state"]
    if state == "OUTAGE":
        sim_state["bat_v_base"] -= 0.05
        prev_time = sim_state["time_base"]
        sim_state["time_base"] -= 60
        sim_state["temp_base"] += 0.05
        
        # Trigger battery depletion logic
        if sim_state["time_base"] <= 0:
            sim_state["time_base"] = 0
            sim_state["out_v_base"] = 0.0
            sim_state["out_a_base"] = 0.0
            if prev_time > 0: # Only tick the checkbox exactly when it dies
                dpg.set_value("ui_w_6", True) # Shutdown triggered
            
    elif state == "RECOVERY":
        sim_state["bat_v_base"] += 0.1
        sim_state["time_base"] += 60
        sim_state["temp_base"] -= 0.1
        if sim_state["bat_v_base"] > 27.2: sim_state["bat_v_base"] = 27.2
        if sim_state["time_base"] > 7200: sim_state["time_base"] = 7200
        if sim_state["temp_base"] < 25.0: sim_state["temp_base"] = 25.0

    # Apply jitter. Jitter is suppressed if base drops to 0.
    sim_state["in_v"] = max(0.0, sim_state["in_v_base"] + random.uniform(-0.08, 0.08)) if sim_state["in_v_base"] > 0 else 0.0
    sim_state["in_a"] = max(0.0, sim_state["in_a_base"] + random.uniform(-0.15, 0.15)) if sim_state["in_a_base"] > 0 else 0.0
    sim_state["out_v"] = max(0.0, sim_state["out_v_base"] + random.uniform(-0.04, 0.04)) if sim_state["out_v_base"] > 0 else 0.0
    sim_state["out_a"] = max(0.0, sim_state["out_a_base"] + random.uniform(-0.10, 0.10)) if sim_state["out_a_base"] > 0 else 0.0
    sim_state["bat_v"] = max(0.0, sim_state["bat_v_base"] + random.uniform(-0.02, 0.02)) if sim_state["bat_v_base"] > 0 else 0.0
    sim_state["chg_a"] = max(0.0, sim_state["chg_a_base"] + random.uniform(-0.05, 0.05)) if sim_state["chg_a_base"] > 0 else 0.0
    sim_state["temp"] = sim_state["temp_base"] + random.uniform(-0.2, 0.2)
    sim_state["time"] = sim_state["time_base"]

    if not dpg.is_item_active("ui_in_v"): dpg.set_value("ui_in_v", sim_state["in_v"])
    if not dpg.is_item_active("ui_in_a"): dpg.set_value("ui_in_a", sim_state["in_a"])
    if not dpg.is_item_active("ui_out_v"): dpg.set_value("ui_out_v", sim_state["out_v"])
    if not dpg.is_item_active("ui_out_a"): dpg.set_value("ui_out_a", sim_state["out_a"])
    if not dpg.is_item_active("ui_bat_v"): dpg.set_value("ui_bat_v", sim_state["bat_v"])
    if not dpg.is_item_active("ui_chg_a"): dpg.set_value("ui_chg_a", sim_state["chg_a"])
    if not dpg.is_item_active("ui_temp"): dpg.set_value("ui_temp", sim_state["temp"])
    if not dpg.is_item_active("ui_time"): dpg.set_value("ui_time", sim_state["time"])

def read_digital_gui_to_state():
    """Continuously reads checkbox inputs. Allows manual override during auto mode."""
    sim_state["status"] = (1 << 4) if dpg.get_value("ui_status_bat_mode") else 0
    
    w_mask = 0
    for b in UPS_WARNINGS:
        if dpg.get_value(f"ui_w_{b}"): w_mask |= (1 << b)
    sim_state["warnings"] = w_mask
    
    a_mask = 0
    for b in UPS_ALARMS:
        if dpg.get_value(f"ui_a_{b}"): a_mask |= (1 << b)
    sim_state["alarms"] = a_mask
    
    sim_state["hop_active"] = dpg.get_value("ui_hop_active")
    sim_state["hop_dev"] = dpg.get_value("ui_hop_dev")
    sim_state["hop_type"] = dpg.get_value("ui_hop_type")
    sim_state["hop_id"] = dpg.get_value("ui_hop_id")
    sim_state["hop_num"] = dpg.get_value("ui_hop_num")

def read_analog_gui_to_state():
    """Reads analog sliders continuously during manual mode."""
    sim_state["in_v"] = sim_state["in_v_base"] = dpg.get_value("ui_in_v")
    sim_state["in_a"] = sim_state["in_a_base"] = dpg.get_value("ui_in_a")
    sim_state["out_v"] = sim_state["out_v_base"] = dpg.get_value("ui_out_v")
    sim_state["out_a"] = sim_state["out_a_base"] = dpg.get_value("ui_out_a")
    sim_state["bat_v"] = sim_state["bat_v_base"] = dpg.get_value("ui_bat_v")
    sim_state["chg_a"] = sim_state["chg_a_base"] = dpg.get_value("ui_chg_a")
    sim_state["temp"] = sim_state["temp_base"] = dpg.get_value("ui_temp")
    sim_state["time"] = sim_state["time_base"] = dpg.get_value("ui_time")

# =====================================================================
# --- DEAR PYGUI UI ---
# =====================================================================
datablock = ModbusSequentialDataBlock(0x0000, [0] * 8500)
store = ModbusSlaveContext(di=None, co=None, hr=None, ir=datablock)
context = ModbusServerContext(slaves=store, single=True)

threading.Thread(target=modbus_server_thread, args=(context,), daemon=True).start()
threading.Thread(target=update_modbus_registers, args=(context,), daemon=True).start()

dpg.create_context()

with dpg.window(tag="main_window", label="UPS Simulator Control Panel"):
    with dpg.group(horizontal=True):
        
        with dpg.child_window(width=200):
            dpg.add_text("SIMULATION MODE")
            dpg.add_separator()
            dpg.add_checkbox(label="Auto Mode (Jitter)", default_value=True, tag="ui_auto_mode", callback=lambda s,a: sim_state.update({"auto_mode": a}))
            
            dpg.add_spacer(height=10)
            dpg.add_text("MANUAL PRESETS")
            dpg.add_separator()
            dpg.add_button(label="Normal Mains", width=-1, callback=lambda: apply_scenario("NORMAL"))
            dpg.add_spacer(height=2)
            dpg.add_button(label="Outage / Disconnect", width=-1, callback=lambda: apply_scenario("OUTAGE"))
            dpg.add_spacer(height=2)
            dpg.add_button(label="Broken Battery", width=-1, callback=lambda: apply_scenario("BROKEN_BAT"))
            
        with dpg.child_window(width=400):
            dpg.add_text("ANALOG PARAMETERS")
            dpg.add_separator()
            
            dpg.add_slider_float(label="In V", tag="ui_in_v", min_value=0.0, max_value=30.0, default_value=24.1)
            dpg.add_slider_float(label="In A", tag="ui_in_a", min_value=0.0, max_value=60.0, default_value=4.5)
            dpg.add_slider_float(label="Out V", tag="ui_out_v", min_value=0.0, max_value=30.0, default_value=24.0)
            dpg.add_slider_float(label="Out A", tag="ui_out_a", min_value=0.0, max_value=60.0, default_value=4.5)
            dpg.add_slider_float(label="Bat V", tag="ui_bat_v", min_value=0.0, max_value=30.0, default_value=27.2)
            dpg.add_slider_float(label="Chg A", tag="ui_chg_a", min_value=0.0, max_value=60.0, default_value=0.0)
            dpg.add_slider_float(label="Temp °C", tag="ui_temp", min_value=-40.0, max_value=80.0, default_value=25.0)
            dpg.add_slider_int(label="Rem. Time", tag="ui_time", min_value=0, max_value=7200, default_value=7200)

        with dpg.child_window(width=-1):
            dpg.add_text("DIGITAL BITS & ALARMS")
            dpg.add_separator()
            dpg.add_checkbox(label="Status: Battery Mode Active", tag="ui_status_bat_mode")
            dpg.add_spacer(height=5)
            
            dpg.add_text("Warnings (0x2015)")
            for bit, desc in UPS_WARNINGS.items():
                dpg.add_checkbox(label=f"Bit {bit}: {desc}", tag=f"ui_w_{bit}")
                
            dpg.add_spacer(height=5)
            dpg.add_text("Alarms (0x2016)")
            for bit, desc in UPS_ALARMS.items():
                dpg.add_checkbox(label=f"Bit {bit}: {desc}", tag=f"ui_a_{bit}")
                
            dpg.add_spacer(height=5)
            dpg.add_text("Hopping Alarm Config (0x2018)")
            dpg.add_checkbox(label="Active", tag="ui_hop_active")
            with dpg.group(horizontal=True):
                dpg.add_input_int(label="Dev", tag="ui_hop_dev", width=50, step=0)
                dpg.add_input_int(label="Type", tag="ui_hop_type", width=50, step=0)
                dpg.add_input_int(label="ID", tag="ui_hop_id", width=50, step=0)
                dpg.add_input_int(label="Num", tag="ui_hop_num", width=50, step=0)

dpg.create_viewport(title='Phoenix QUINT4 Simulator Control', width=1050, height=650)
dpg.setup_dearpygui()
dpg.show_viewport()
dpg.set_primary_window("main_window", True)

last_tick = time.time()
while dpg.is_dearpygui_running():
    current_time = time.time()
    
    # The GUI is always the absolute source of truth for checkboxes
    read_digital_gui_to_state()
    
    if sim_state["auto_mode"]:
        # If user drags an analog slider during Auto Mode, instantly make it the new jitter baseline
        if dpg.is_item_active("ui_in_v"): sim_state["in_v_base"] = dpg.get_value("ui_in_v")
        if dpg.is_item_active("ui_in_a"): sim_state["in_a_base"] = dpg.get_value("ui_in_a")
        if dpg.is_item_active("ui_out_v"): sim_state["out_v_base"] = dpg.get_value("ui_out_v")
        if dpg.is_item_active("ui_out_a"): sim_state["out_a_base"] = dpg.get_value("ui_out_a")
        if dpg.is_item_active("ui_bat_v"): sim_state["bat_v_base"] = dpg.get_value("ui_bat_v")
        if dpg.is_item_active("ui_chg_a"): sim_state["chg_a_base"] = dpg.get_value("ui_chg_a")
        if dpg.is_item_active("ui_temp"): sim_state["temp_base"] = dpg.get_value("ui_temp")
        if dpg.is_item_active("ui_time"): sim_state["time_base"] = dpg.get_value("ui_time")

        if current_time - last_tick > 0.5:
            process_auto_tick()
            last_tick = current_time
    else:
        # User fully controls analog sliders without jitter overrides
        read_analog_gui_to_state()
        
    dpg.render_dearpygui_frame()

dpg.destroy_context()