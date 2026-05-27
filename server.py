from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
import os
import re
import json
from datetime import datetime
import time
import argparse
import bleach

parser = argparse.ArgumentParser(description='Set webserver port and machine name')
parser.add_argument('-port', type=int, nargs='?', default=8000, help='Port for the webserver (default: 8000)')
parser.add_argument('-machine_name', type=str, required=True, help='Name of the machine')
args = parser.parse_args()
port = args.port
machine_name = args.machine_name
print(f'Starting webserver on port {port} for machine {machine_name}')

app = Flask(__name__)
CORS(app)

log_cache = {'files': {}, 'order': []}
bandwidth_cache = {'files': {}, 'order': []}
error_cache = {'files': {}, 'order': [], 'dismissed_errors': []}

EARNINGS_REPORT_REGEX = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} [+-]\d{2}:\d{2}) \[INF\] Predicted Earnings Report: ([\d.]+) from \(([^)]+)\)"
)
WALLET_BALANCE_REGEX = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} [+-]\d{2}:\d{2}) \[INF\] Wallet: Current\(([\d.]+)\), Predicted\(([-\d.]+)\)"
)
BANDWIDTH_REGEX = re.compile(
    r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} [+-]\d{2}:\d{2}) \[INF\] \{.*?"BidirThroughput":([\d.]+)'
)
ERROR_REGEX = re.compile(
    r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} [+-]\d{2}:\d{2}) \[WRN\] Node Compatibility Workload Failure (.*?) NodeCompatibilityMessage {(.*?)}',
    re.DOTALL
)
SYSTEM_INFO_JSON_PATTERN = re.compile(r"\[INF\] StaticData: (\{.*\})")

def parse_file(file_path, start_line=0):
    salad_data = []
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.readlines()[start_line:]

        for match in EARNINGS_REPORT_REGEX.finditer(''.join(content)):
            salad_data.append({
                'timestamp': match.group(1),
                'earnings': float(match.group(2)),
                'containerId': match.group(3)
            })

        for match in WALLET_BALANCE_REGEX.finditer(''.join(content)):
            salad_data.append({
                'timestamp': match.group(1),
                'currentBalance': float(match.group(2)),
                'predictedBalance': float(match.group(3))
            })

        if "Bandwidth-SGS-" in file_path:
            for match in BANDWIDTH_REGEX.finditer(''.join(content)):
                salad_data.append({
                    'timestamp': match.group(1),
                    'BidirThroughput': float(match.group(2)) / (280000*8)  # Convert from bits/30s to MB/s
                })

    return salad_data, len(content)

def search_logs(log_dir):
    start_time = time.perf_counter()
    salad_data = []

    for file_path in log_cache['order']:
        salad_data.extend(log_cache['files'][file_path]['data'])
    for file_path in bandwidth_cache['order']:
        salad_data.extend(bandwidth_cache['files'][file_path]['data'])

    log_files = []
    for root, dirnames, filenames in os.walk(log_dir):
        dirnames[:] = [d for d in dirnames if d not in ['ndm', 'systeminformation']]
        log_files.extend(
            os.path.join(root, file)
            for file in filenames
            if file.endswith(('.txt', '.log')) and "Bandwidth-SGS-" not in root
        )
    log_files.sort(key=os.path.getmtime, reverse=True)
    log_files = log_files[:3]

    log_files_to_process = []
    for file_path in log_files:
        last_processed_time = log_cache['files'].get(file_path, {}).get('last_modified', None)
        if last_processed_time is None or os.path.getmtime(file_path) > last_processed_time:
            log_files_to_process.append(file_path)

    new_data_found = False
    for file_path in log_files_to_process:
        start_line = log_cache['files'].get(file_path, {}).get('last_line', 0)
        new_data, lines_read = parse_file(file_path, start_line)
        if new_data:
            print(f"{datetime.now()}: New data found in {file_path}")
            new_data_found = True
        salad_data.extend(new_data)
        log_cache['files'][file_path] = {
            'last_line': start_line + lines_read,
            'data': log_cache['files'].get(file_path, {}).get('data', []) + new_data,
            'last_modified': os.path.getmtime(file_path)  # Store the modification time
        }
        if file_path not in log_cache['order']:
            log_cache['order'].append(file_path)
            if len(log_cache['order']) > 3:
                oldest_file = log_cache['order'].pop(0)
                log_cache['files'].pop(oldest_file, None)

    bandwidth_files = [
        os.path.join(root, file)
        for root, _, filenames in os.walk(log_dir)
        if "Bandwidth-SGS-" in root
        for file in filenames if file.endswith(('.txt', '.log'))
    ]
    bandwidth_files.sort(key=os.path.getmtime, reverse=True)
    bandwidth_files = bandwidth_files[:2]

    bandwidth_files_to_process = []
    for file_path in bandwidth_files:
        last_processed_time = bandwidth_cache['files'].get(file_path, {}).get('last_modified', None)
        if last_processed_time is None or os.path.getmtime(file_path) > last_processed_time:
            bandwidth_files_to_process.append(file_path)

    for file_path in bandwidth_files_to_process:
        start_line = bandwidth_cache['files'].get(file_path, {}).get('last_line', 0)
        new_data, lines_read = parse_file(file_path, start_line)
        if new_data:
            print(f"{datetime.now()}: New bandwidth data found in {file_path}")
            new_data_found = True
        salad_data.extend(new_data)
        bandwidth_cache['files'][file_path] = {
            'last_line': start_line + lines_read,
            'data': bandwidth_cache['files'].get(file_path, {}).get('data', []) + new_data,
            'last_modified': os.path.getmtime(file_path)  # Store the modification time
        }
        if file_path not in bandwidth_cache['order']:
            bandwidth_cache['order'].append(file_path)
            if len(bandwidth_cache['order']) > 2:
                oldest_file = bandwidth_cache['order'].pop(0)
                bandwidth_cache['files'].pop(oldest_file, None)

    if not new_data_found:
        print(f"{datetime.now()}: No new data found. Returning cached data.")

    # Sort the final data list
    salad_data.sort(key=lambda x: datetime.strptime(x['timestamp'], '%Y-%m-%d %H:%M:%S.%f %z'))
    print(f"Log processing completed in {(time.perf_counter() - start_time) * 1000:.2f} ms")
    return salad_data

def check_errors(log_dir):
    files = [
        os.path.join(root, file)
        for root, _, filenames in os.walk(log_dir)
        for file in filenames if file.endswith(('.txt', '.log')) and "Bandwidth-SGS-" not in root
    ]
    files.sort(key=os.path.getmtime, reverse=True)
    files = files[:3]  # Check the most recent 3 files

    errors = []
    for file_path in files:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for match in ERROR_REGEX.finditer(f.read()):
                error_timestamp = match.group(1)
                if error_timestamp not in error_cache['dismissed_errors']:
                    error_cache['files'][error_timestamp] = {
                        'timestamp': error_timestamp,
                        'machine_name': machine_name,
                        'error_message': match.group(3)
                    }
                    errors.append(error_cache['files'][error_timestamp])

    return errors

def get_system_info():
    system_info_dir = r"C:\ProgramData\Salad\logs\systeminformation"
    system_info = {"CPU": "Unknown", "GPU": "Unknown", "RAM": "Unknown", "OS": "Unknown"}
    try:
        files = [os.path.join(system_info_dir, f) for f in os.listdir(system_info_dir) if f.endswith(".log") or f.endswith(".txt")]
        if not files: return system_info
        files.sort(key=os.path.getmtime, reverse=True)
        with open(files[0], "r", encoding="utf-8", errors='ignore') as f:
            for line in f:
                json_match = SYSTEM_INFO_JSON_PATTERN.search(line)
                if json_match:
                    data = json.loads(json_match.group(1))
                    if "cpu" in data:
                        cpu = data["cpu"]
                        system_info["CPU"] = f"{cpu.get('manufacturer', '')} {cpu.get('brand', '')}".strip()
                    if "graphics" in data and "controllers" in data["graphics"] and data["graphics"]["controllers"]:
                        gpu = data["graphics"]["controllers"][0]
                        system_info["GPU"] = f"{gpu.get('vendor', '')} {gpu.get('model', '')}".strip()
                    if "memLayout" in data:
                        total_ram_bytes = sum(module.get("size", 0) for module in data["memLayout"])
                        system_info["RAM"] = f"{total_ram_bytes // (1024 ** 3)} GB"
                    if "os" in data:
                        system_info["OS"] = data["os"].get("distro", "Unknown")
                    break
    except Exception: pass
    return system_info

# Flask routes
@app.route('/api/salad-data', methods=['GET'])
def get_salad_data():
    return jsonify(search_logs('C:\\ProgramData\\Salad\\logs'))

@app.route('/api/error-status', methods=['GET'])
def get_error_status():
    return jsonify(check_errors('C:\\ProgramData\\Salad\\logs'))

@app.route('/api/system-info', methods=['GET'])
def api_system_info():
    return jsonify(get_system_info())

@app.route('/api/dismiss-error', methods=['POST'])
def dismiss_error():
    error_timestamp = bleach.clean(request.json.get('timestamp', ''), strip=True)
    if error_timestamp not in error_cache['dismissed_errors']:
        error_cache['dismissed_errors'].append(error_timestamp)
    return jsonify({'status': 'success', 'dismissed': error_timestamp})

@app.route('/')
def serve_index():
    return send_from_directory('', 'index.html')

if __name__ == '__main__':
    app.run(debug=False, port=port)