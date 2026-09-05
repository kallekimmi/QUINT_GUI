from pymodbus.server import StartTcpServer
from pymodbus.datastore import ModbusSequentialDataBlock, ModbusSlaveContext, ModbusServerContext
import threading
import time
import random

def simulate_ups_behavior(context):
    """Background thread simulating a realistic UPS charge/discharge cycle with sensor noise."""
    register_block = context[0].store['i'] # 'i' = Input Registers (Function code 04)
    # KORRIGERING: pymodbus datastore är 1-indexerad internt.
    # För att simulera nätverksadress 0x2000 måste vi skriva till 0x2001.
    base_addr = 0x2001
    
    tick = 0
    state = "MAINS"
    
    # Internal baseline variables
    bat_v_base = 27.2
    rem_time = 7200
    bat_temp_base = 298.15 # ~25°C in Kelvin[cite: 1]
    
    while True:
        tick += 1
        
        # --- STATE MACHINE TRANSITIONS ---
        # Repeats a full cycle every 90 seconds
        if tick == 1:
            state = "MAINS"
            print("System State: MAINS ONLINE - Battery Full")
        elif tick == 30:
            state = "OUTAGE"
            print("System State: POWER OUTAGE - Running on Battery!")
        elif tick == 60:
            state = "RECOVERY"
            print("System State: MAINS RESTORED - Charging Battery")
        elif tick >= 90:
            tick = 0
            continue

        # --- BASELINE LOGIC ---
        if state == "MAINS":
            status_0x2000 = 0
            warn_0x2015 = 0
            in_v = 24.1
            in_a = 4.5
            out_v = 24.0
            out_a = 4.5
            chg_a = 0.02             # Float charging (trickle)
            bat_v_base = 27.2
            rem_time = 7200
            bat_temp_base = 298.15
            hop_reg = 0
            
        elif state == "OUTAGE":
            status_0x2000 = (1 << 4) # Bit 4: Battery mode active[cite: 1]
            warn_0x2015 = (1 << 4)   # Bit 4: Warning Battery mode active[cite: 1]
            in_v = 0.0               # Grid is dead
            in_a = 0.0
            out_v = bat_v_base - 0.2 # Output tracks battery voltage minus small drop
            out_a = 4.5              # Load remains the same
            chg_a = 0.0
            bat_v_base -= 0.05       # Battery discharges slowly
            rem_time -= 120          # Time drops rapidly for demonstration
            bat_temp_base += 0.05    # Battery warms up slightly when discharging
            
            # Simulate Hopping Alarm: QUINT4 Power Supply (Device 7) -> No AC input voltage (Alarm 0, Type 4)[cite: 1]
            hop_reg = (1 << 31) | (7 << 15) | (0 << 8) | (4 << 5) | 1
            
        elif state == "RECOVERY":
            status_0x2000 = 0
            warn_0x2015 = 0
            in_v = 24.1
            in_a = 6.5               # Pulling extra current to charge
            out_v = 24.0
            out_a = 4.5
            chg_a = 2.0              # Bulk charging
            bat_v_base += 0.1        # Battery recovering rapidly
            rem_time += 120
            bat_temp_base += 0.1     # Battery gets warmer during bulk charge
            hop_reg = 0
            
            # Cap limits
            if bat_v_base > 27.2: bat_v_base = 27.2
            if rem_time > 7200: rem_time = 7200

        # --- ADD REALISTIC SENSOR JITTER ---
        # Using random.uniform to simulate ADC noise on the electronics
        noisy_in_v = max(0.0, in_v + random.uniform(-0.08, 0.08)) if in_v > 0 else 0.0
        noisy_in_a = max(0.0, in_a + random.uniform(-0.15, 0.15)) if in_a > 0 else 0.0
        noisy_out_v = max(0.0, out_v + random.uniform(-0.04, 0.04))
        noisy_out_a = max(0.0, out_a + random.uniform(-0.10, 0.10))
        noisy_bat_v = max(0.0, bat_v_base + random.uniform(-0.02, 0.02))
        noisy_chg_a = max(0.0, chg_a + random.uniform(-0.05, 0.05)) if chg_a > 0 else 0.0
        noisy_bat_t = bat_temp_base + random.uniform(-0.2, 0.2)

        # --- WRITE TO MODBUS REGISTERS ---
        # Scale to millivolts/milliamps/Kelvin as per Phoenix Contact specs[cite: 1]
        register_block.setValues(base_addr + 0x00, [status_0x2000])
        register_block.setValues(base_addr + 0x04, [int(noisy_in_v * 1000)])  
        register_block.setValues(base_addr + 0x05, [int(noisy_in_a * 1000)])  
        register_block.setValues(base_addr + 0x06, [int(noisy_out_v * 1000)]) 
        register_block.setValues(base_addr + 0x07, [int(noisy_out_a * 1000)]) 
        register_block.setValues(base_addr + 0x0A, [int(noisy_bat_v * 1000)]) 
        register_block.setValues(base_addr + 0x0B, [int(noisy_chg_a * 1000)]) 
        
        register_block.setValues(base_addr + 0x0D, [int(noisy_bat_t)])
        register_block.setValues(base_addr + 0x0E, [int(308 + random.uniform(-0.5, 0.5))]) # Device temp jitter
        
        # 32-bit integer span for Remaining Time[cite: 1]
        time_int = int(rem_time)
        register_block.setValues(base_addr + 0x10, [time_int & 0xFFFF, (time_int >> 16) & 0xFFFF])
        register_block.setValues(base_addr + 0x15, [warn_0x2015])
        register_block.setValues(base_addr + 0x18, [hop_reg & 0xFFFF, (hop_reg >> 16) & 0xFFFF])

        time.sleep(1.0)

# Initialize memory block with zeros up to address 0x2050 (8272 decimal)
datablock = ModbusSequentialDataBlock(0x0000, [0] * 8500)
store = ModbusSlaveContext(di=None, co=None, hr=None, ir=datablock)
context = ModbusServerContext(slaves=store, single=True)

print("Starting realistic UPS Simulator with sensor noise on 127.0.0.1:5020...")
threading.Thread(target=simulate_ups_behavior, args=(context,), daemon=True).start()
StartTcpServer(context=context, address=("127.0.0.1", 5020))