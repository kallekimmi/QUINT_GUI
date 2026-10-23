QUINT4 UPS Modbus Monitor & Simulator
An interactive suite built with Dear PyGui and PyModbus designed to monitor, visualize, and simulate telemetry from a Phoenix Contact QUINT4 UPS (or compatible device) over Modbus TCP.
The repository includes two main applications:
UPS Monitor (QUINT_GUI.py): A Modbus TCP client for monitoring live UPS data.
UPS Simulator (SimulateUPS.py): A Modbus TCP server simulating a virtual UPS for development and testing.
System Requirements & Setup
Target Environment
Python: 3.14.7
PyModbus: 3.16.1
Dear PyGui: 2.3.1
Installation
Install all required dependencies using pip:
```bash
python -m pip install dearpygui pymodbus==3.16.1 pyinstaller
```
dearpygui: Handles GUI rendering, controls, and dynamic telemetry plotting.
pymodbus: Manages Modbus TCP client/server communication.
pyinstaller: (Optional) Used to package the GUI application into a standalone Windows executable.
Features & Tools
1. UPS Monitor (QUINT_GUI.py)
A graphical Modbus TCP client that connects to a physical UPS or simulator to read telemetry from Input Registers.
Real-Time Monitoring: Displays live measurements for input/output voltage, current, battery voltage, temperature, and remaining runtime.
Telemetry Plots: Graphically visualizes historical data trends over time.
Status & Diagnostics: Decodes status registers, warning/alarm bitmasks (0x2015-0x2017), and hopping alarms (0x2018-0x2019).
Visual Feedback: Color-coded GUI indicators for rapid identification of state changes and active alarms.
CLI Configuration: Accepts command-line arguments (e.g., --ip, --port, --interval, --points).
2. UPS Simulator (SimulateUPS.py)
A standalone Modbus TCP server simulating a virtual QUINT4 UPS.
Automated Simulation: Automatically cycles through realistic operating states with added measurement jitter:
MAINS: Normal mains power operation.
OUTAGE: Mains power failure, battery drain, decreasing runtime, and rising temperature.
RECOVERY: Restored mains power, battery recharging, and thermal cooling.
Preset Scenarios: Quick-select modes for testing specific states:
Normal Mains: Sets nominal operating values.
Outage / Disconnect: Simulates power loss and triggers battery operation flags.
Broken Battery: Triggers abnormal battery voltage warnings and alarms.
Manual Controls: Interactive sliders and toggles for manual adjustment of register values and digital flags.
Dynamic Input Registers: Exposes addresses 0x2000 through 0x2019. Registers update dynamically upon client read requests.
Default Simulator Network Settings:
Host: 127.0.0.1 (Localhost only)
Port: 502 (Standard Modbus TCP port - may require elevated privileges)
Unit ID: 1
Register Type: Input Registers (Function Code 04)
Modbus Register Map
The simulator implements the following Modbus register mapping:
Register Address	Description	Data Representation
0x2000	UPS Status	16-bit status bitmask
0x2004	Input Voltage	Voltage * 1000
0x2005	Input Current	Current * 1000
0x2006	Output Voltage	Voltage * 1000
0x2007	Output Current	Current * 1000
0x200A	Battery Voltage	Voltage * 1000
0x200B	Charging Current	Current * 1000
0x200D	Temperature	Temperature in Kelvin
0x200E	Secondary Temperature	Kelvin value + 5
0x2010	Remaining Runtime (Low Word)	Lower 16 bits
0x2011	Remaining Runtime (High Word)	Upper 16 bits
0x2015	UPS Warnings	16-bit warning bitmask
0x2016	UPS Alarms (Low Word)	Lower 16 bits
0x2017	UPS Alarms (High Word)	Upper 16 bits
0x2018	Hopping Alarm (Low Word)	Lower 16 bits
0x2019	Hopping Alarm (High Word)	Upper 16 bits
Register Addressing & Data Notes
Addressing: The table lists logical addresses. For example, 0x2000 corresponds to decimal 8192 (or 38193 in 1-based 3xxxx notation used by some clients).
Scaling: Analog integers must be divided by 1000 by the client to obtain standard engineering units (e.g., Volts, Amperes).
32-Bit Values: Runtime and alarm values span two consecutive 16-bit registers (Low Word first, High Word second).
Running the Applications
Testing with the Simulator
Launch the simulator in a terminal window:
```bash
   python SimulateUPS.py
   ```
Launch the monitor in a second terminal window:
```bash
   python QUINT_GUI.py --ip 127.0.0.1 --port 502
   ```
Connecting to a Physical UPS
To monitor a physical device on your network, pass the target IP address to the monitor:
```bash
python QUINT_GUI.py --ip 192.168.19.23 --port 502
```
Ensure Modbus TCP is enabled on the device and firewall ports are open.
Packaging a Windows Executable
To bundle QUINT_GUI.py into a single standalone Windows executable using PyInstaller (ensure favicon.ico is present in the working directory):
```bash
pyinstaller --onefile --noconsole --icon=favicon.ico --add-data "favicon.ico;." QUINT_GUI.py
```
Alternatively, execute Build.bat if configured for your project.
Troubleshooting
Problem	Possible Cause / Solution
Monitor cannot connect to simulator	- Confirm the simulator is running.<br>- Verify IP (127.0.0.1) and port (502).<br>- Check if another process uses port 502 and verify firewall rules.
Register values do not change	- Ensure you read as Input Registers (Function Code 04).<br>- Check logical register addresses.<br>- Adjust a slider in the simulator and re-read.
Alarm values appear incorrect	- Verify Low and High word combination order for 32-bit values.<br>- Check bit definitions against UPS documentation.
PyModbus API error	- Ensure PyModbus version is 3.16.1 (`python -m pip show pymodbus`).
Project Structure
```text
QUINT4-UPS/
├── QUINT_GUI.py       # Main Application (Monitor GUI)
├── SimulateUPS.py   # UPS Simulator (Modbus Server GUI)
├── favicon.ico        # Application Icon (Optional)
├── Build.bat          # PyInstaller Build Script (Optional)
├── README.md          # Project Documentation
├── build/             # PyInstaller Temporary Files
└── dist/              # Output Directory for Exe
```
Note: The simulator is intended for software development and integration testing. Always verify register addresses, scaling, and bit definitions against official Phoenix Contact documentation before validating hardware.
