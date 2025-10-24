import os
os.environ["BLEAK_BACKEND"] = "dotnet"

import asyncio
from bleak import BleakScanner, BleakClient
from collections import deque
import matplotlib.pyplot as plt

# BLE config
DEVICE_NAME = "ESP32C3_ACC_PPG"
CHARACTERISTIC_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"

# Buffers
history = 100
x_vals, y_vals, z_vals = deque(maxlen=history), deque(maxlen=history), deque(maxlen=history)
ir_vals, red_vals = deque(maxlen=history), deque(maxlen=history)

# Global plot objects
fig = None
ax_x, ax_y, ax_z = None, None, None
ax_ir, ax_red = None, None
line_x, line_y, line_z = None, None, None
line_ir, line_red = None, None

def update_plot():
	# Update each subplot
	line_x.set_ydata(x_vals)
	line_x.set_xdata(range(len(x_vals)))
	ax_x.relim()
	ax_x.autoscale_view(True, True, True)

	line_y.set_ydata(y_vals)
	line_y.set_xdata(range(len(y_vals)))
	ax_y.relim()
	ax_y.autoscale_view(True, True, True)

	line_z.set_ydata(z_vals)
	line_z.set_xdata(range(len(z_vals)))
	ax_z.relim()
	ax_z.autoscale_view(True, True, True)

	line_ir.set_ydata(ir_vals)
	line_ir.set_xdata(range(len(ir_vals)))
	ax_ir.relim()
	ax_ir.autoscale_view(True, True, True)

	line_red.set_ydata(red_vals)
	line_red.set_xdata(range(len(red_vals)))
	ax_red.relim()
	ax_red.autoscale_view(True, True, True)

	plt.draw()
	plt.pause(0.01)

def handle_notification(sender, data):
	try:
		text = data.decode("utf-8").strip()
		ax_, ay_, az_, ir, red = map(float, text.split(","))
		x_vals.append(ax_)
		y_vals.append(ay_)
		z_vals.append(az_)
		ir_vals.append(ir)
		red_vals.append(red)
		update_plot()
	except Exception as e:
		print(f"💥 Parse error: {e} → {data}")

async def main():
	print(f"🔍 Scanning for {DEVICE_NAME}...")
	device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=10.0)

	if not device:
		print("❌ Device not found.")
		return

	print(f"✅ Found {DEVICE_NAME}, attempting to connect...")
	async with BleakClient(device) as client:
		await asyncio.sleep(2)

		if await client.is_connected():
			print(f"🎉 Successfully connected to {DEVICE_NAME}!")

			# Set up 3x2 plot grid
			global fig, ax_x, ax_y, ax_z, ax_ir, ax_red
			global line_x, line_y, line_z, line_ir, line_red

			plt.ion()
			fig, axes = plt.subplots(3, 2, figsize=(10, 8), sharex=True)
			(ax_x, ax_ir), (ax_y, ax_red), (ax_z, empty_ax) = axes
			fig.delaxes(empty_ax)

			# Left column – Accelerometer
			line_x, = ax_x.plot([], [], label='X', color='blue')
			ax_x.set_ylabel("X (m/s²)")
			ax_x.set_title("Accel X")

			line_y, = ax_y.plot([], [], label='Y', color='green')
			ax_y.set_ylabel("Y (m/s²)")
			ax_y.set_title("Accel Y")

			line_z, = ax_z.plot([], [], label='Z', color='red')
			ax_z.set_ylabel("Z (m/s²)")
			ax_z.set_xlabel("Samples")
			ax_z.set_title("Accel Z")

			# Right column – PPG
			line_ir, = ax_ir.plot([], [], label='IR', color='purple')
			ax_ir.set_ylabel("IR")
			ax_ir.set_title("PPG IR")

			line_red, = ax_red.plot([], [], label='RED', color='orange')
			ax_red.set_ylabel("RED")
			ax_red.set_xlabel("Samples")
			ax_red.set_title("PPG RED")

			await client.start_notify(CHARACTERISTIC_UUID, handle_notification)
			print("📡 Receiving notifications...")

			while plt.fignum_exists(fig.number):
				await asyncio.sleep(0.1)

		else:
			print("❌ Failed to connect.")

if __name__ == "__main__":
	try:
		asyncio.run(main())
	except Exception as e:
		print(f"💥 Exception: {e}")
