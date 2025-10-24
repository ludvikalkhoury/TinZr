import requests
import webbrowser
import time

esp_ip = "http://172.20.10.2"
html_url = esp_ip + "/"
json_url = esp_ip + "/data"
check_interval = 1
max_attempts = 10

def is_esp32_up(ip):
	try:
		resp = requests.get(ip, timeout=2)
		print("🔗 Status:", resp.status_code)
		print("📄 Snippet:", resp.text[:200])
		return resp.status_code == 200 and ("IMU" in resp.text or "html" in resp.headers.get("Content-Type", ""))
	except Exception as e:
		print("❌ Exception:", e)
		return False

def fetch_json(url):
	try:
		resp = requests.get(url, timeout=2)
		print("📦 JSON Data:", resp.json())
	except Exception as e:
		print("⚠️ Failed to get JSON:", e)

def main():
	print(f"🔍 Checking ESP32 at {esp_ip}")
	for attempt in range(max_attempts):
		if is_esp32_up(html_url):
			print("✅ ESP32 is online!")
			fetch_json(json_url)
			webbrowser.open(html_url)
			return
		else:
			print(f"⏳ Attempt {attempt + 1} failed. Retrying in {check_interval}s...")
			time.sleep(check_interval)
	print("❌ ESP32 not responding after several attempts.")

if __name__ == "__main__":
	main()
