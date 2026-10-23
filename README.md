QUINT4 UPS Modbus Monitor & Simulator

This project contains two interactive applications built with Dear PyGui and PyModbus. The applications are designed to monitor, visualize, and simulate telemetry from a Phoenix Contact QUINT4 UPS or a compatible device over Modbus TCP.

The project consists of a Modbus TCP client for monitoring UPS data and a Modbus TCP server that simulates a virtual UPS for development and testing.

System Requirements & Environment

The project targets the following environment:

Python 3.14.7
PyModbus 3.16.1
Dear PyGui 2.3.1
Dependencies & Installation

Install the required Python packages using pip:

python -m pip install dearpygui pymodbus==3.16.1 pyinstaller


The packages serve the following purposes:

dearpygui: Provides the graphical user interface, controls, and telemetry visualization.
pymodbus: Handles Modbus TCP client and server communication.
pyinstaller: Optional dependency used to package the monitor as a standalone Windows executable.

Make sure that the Python interpreter used to run the applications is the same interpreter into which the dependencies were installed.

Features & Included Tools
1. UPS Monitor (UPSModbus.py)

A graphical Modbus TCP client that connects to a UPS or the simulator and reads telemetry from its Input Registers.

Features include:

Real-Time Monitoring: Displays UPS measurements, including input and output voltage, current, battery voltage, temperature, and remaining runtime.
Telemetry Plots: Visualizes measurement history to help identify trends and changes in operating conditions.
Status Monitoring: Reads the UPS status register and displays the decoded operating state.
Warnings & Alarms: Decodes warning and alarm bitmasks, including the registers at 0x2015, 0x2016, and 0x2017.
Hopping Alarm Monitoring: Reads the hopping alarm registers at 0x2018 and 0x2019.
Visual Feedback: Uses GUI indicators to make changing measurements, warnings, and alarms easier to identify.
Command-Line Configuration: Supports connection and monitoring options such as --ip, --port, --interval, and --points, as implemented in the monitor script.

The monitor can connect to a physical UPS or to the simulator running on the same computer.

2. UPS Simulator (SimulateUPS_2.py)

A standalone Modbus TCP server that simulates a virtual Phoenix Contact QUINT4 UPS. It allows the monitor and other Modbus clients to be tested without requiring physical UPS hardware.

Automatic Simulation

When Auto Mode is enabled, the simulator cycles through three operating states:

MAINS: Simulates normal mains operation.
OUTAGE: Simulates a mains power failure, battery operation, decreasing battery voltage, reduced remaining runtime, and increasing temperature.
RECOVERY: Simulates restored mains power, battery recovery, increasing remaining runtime, and decreasing temperature.

The automatic simulation also introduces small random variations, known as jitter, to analog measurements to make the reported values appear more realistic.

Manual Scenarios

The graphical interface provides predefined scenarios:

Normal Mains: Sets nominal values for normal operation.
Outage / Disconnect: Simulates a loss of input power and activates battery-mode indicators.
Broken Battery: Simulates abnormal battery voltage and activates selected warning and alarm bits.

Selecting a manual scenario disables Auto Mode and applies the corresponding analog values and digital flags.

Manual Controls

When Auto Mode is disabled, the operator can control the analog measurements using the GUI sliders and change digital status, warning, alarm, and hopping alarm settings using the available controls.

These values are exposed through the simulator's Modbus Input Registers.

Dynamic Input Registers

The simulator uses PyModbus's simulated device and data structures to expose the register map.

Input Register values are refreshed when a Modbus client reads them. This allows measurements, status flags, warnings, alarms, and hopping alarm data to reflect the current simulation state rather than remaining at their initial values.

The simulator exposes the register range 0x2000 through 0x2019. Only the registers defined by the simulation register map contain meaningful values; reserved addresses return zero.

Default connection settings:

Host: 127.0.0.1
Port: 502
Unit ID: 1
Register type: Input Registers (Function Code 04)

The default host address restricts connections to the local computer. To allow other computers on the network to connect, the server bind address and firewall configuration must be adjusted accordingly.

Port 502 is the standard Modbus TCP port and may require elevated privileges or additional configuration on some systems.

Modbus Register Map

The following table describes the register mapping implemented by the simulator.

Register Address	Description	Data Representation
0x2000	UPS status	16-bit status bitmask
0x2004	Input voltage	Voltage × 1000
0x2005	Input current	Current × 1000
0x2006	Output voltage	Voltage × 1000
0x2007	Output current	Current × 1000
0x200A	Battery voltage	Voltage × 1000
0x200B	Charging current	Current × 1000
0x200D	Temperature	Temperature in Kelvin
0x200E	Secondary temperature value	Kelvin value + 5
0x2010	Remaining runtime, low word	Lower 16 bits
0x2011	Remaining runtime, high word	Upper 16 bits
0x2015	UPS warnings	16-bit warning bitmask
0x2016	UPS alarms, low word	Lower 16 bits
0x2017	UPS alarms, high word	Upper 16 bits
0x2018	Hopping alarm, low word	Lower 16 bits
0x2019	Hopping alarm, high word	Upper 16 bits
Register Addressing Notes

The addresses above are the simulator's logical Modbus register addresses. Some client applications display addresses using one-based references or 3xxxx notation. Ensure that the monitor and any third-party Modbus client use the correct addressing convention.

For example, the simulator's logical address 0x2000 is decimal 8192.

Analog values are scaled as described in the table. A client reading a voltage register must divide the returned integer by 1000 to obtain the voltage in the simulator's engineering units.

The runtime and alarm values occupy two consecutive 16-bit registers. The lower word is stored first, followed by the upper word.

The status, warning, alarm, and hopping alarm bit definitions are based on the simulator implementation and should be verified against the documentation for the exact QUINT4 model when testing against physical hardware.

How to Run
Testing with the Simulator

Start the simulator in the first terminal:

python SimulateUPS_2.py


The simulator starts a Modbus TCP server on 127.0.0.1:502 and opens the graphical control panel.

Start the monitor in a second terminal:

python UPSModbus.py --ip 127.0.0.1 --port 502


The monitor should now read the simulated Input Registers.

If the server cannot bind to port 502, check whether another process is using the port or whether your operating system requires additional permissions.

Connecting to a Physical UPS

Run the monitor with the IP address of the physical UPS:

python UPSModbus.py --ip 192.168.19.23 --port 502


Replace 192.168.19.23 with the actual IP address of your device.

Make sure that:

The UPS has Modbus TCP enabled.
The computer can reach the UPS over the network.
The configured port and unit identifier match the device configuration.
The monitor uses the register addressing and data scaling specified by the UPS documentation.

The example IP address is illustrative and must be replaced with the address of your own device.

Building a Standalone Windows Executable

PyInstaller can package UPSModbus.py into a standalone Windows executable.

Ensure that favicon.ico is available in the project directory, then run:

pyinstaller --onefile --noconsole --icon=favicon.ico --add-data "favicon.ico;." UPSModbus.py


Alternatively, use Build.bat if it is configured for your project.

The options mean:

--onefile: Packages the application into a single executable.
--noconsole: Hides the console window when launching the graphical application.
--icon=favicon.ico: Sets the executable's icon.
--add-data "favicon.ico;.": Includes the icon as an application resource.

The resulting executable is normally placed in the dist directory.

The icon-loading code in the application must handle PyInstaller's runtime resource directory if the icon is accessed after packaging.

Troubleshooting
The monitor cannot connect to the simulator
Confirm that the simulator is running.
Check that the monitor uses 127.0.0.1 and port 502.
Verify that no other process is occupying port 502.
Check firewall rules and operating system permissions.
Register values do not change
Confirm that the simulator is running the expected version of SimulateUPS_2.py.
Read the values as Input Registers using Modbus Function Code 04.
Check that the client is reading the correct logical addresses.
Change a slider or activate a scenario in the simulator and read the corresponding register again.
Alarm values appear incorrect
Check the bit numbering used by the client.
Confirm that the low and high words are combined in the correct order for 32-bit values.
Verify the alarm bit definitions against the correct UPS documentation.
Python reports a PyModbus API error

Confirm that the installed PyModbus version is 3.16.1:

python -m pip show pymodbus


The simulator uses the SimData and SimDevice APIs available in the targeted PyModbus version. If the library API changes in a later release, the simulator's device configuration or callback interface may need to be updated.

Project Structure

A typical project directory may look like this:

QUINT4-UPS/
├── UPSModbus.py
├── SimulateUPS_2.py
├── favicon.ico
├── Build.bat
├── README.md
├── build/
└── dist/


Files such as favicon.ico and Build.bat are optional unless you use the corresponding executable packaging workflow.

Development Notes

The simulator is intended for software development, GUI testing, and Modbus client integration testing.

It is not a replacement for a physical UPS, and its simulated measurements and alarms should not be used as evidence of real hardware behavior.

Before using the monitor or simulator for hardware validation, verify register addresses, bit definitions, scaling factors, byte/word ordering, and unit identifier settings against the documentation for the exact Phoenix Contact QUINT4 model.
