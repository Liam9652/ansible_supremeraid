#!/usr/bin/env python3
import sys
import json
import traceback

def main():
    try:
        # 从文件读取数据
        with open(sys.argv[1], 'r') as f:
            input_data = f.read().strip()
        nvme_input, scsi_input = input_data.split('|')

        # 解析 JSON 数据
        nvme_parsed = json.loads(nvme_input) if nvme_input else {"Result": []}
        scsi_parsed = json.loads(scsi_input) if scsi_input else {"Result": []}

        # 合并设备列表
        all_devices = nvme_parsed["Result"] + scsi_parsed["Result"]
        all_models = list(set(device['Model'] for device in all_devices))

        result = {
            "display_message": f"Available devices: {len(all_devices)}",
            "prompt_message": "Enter your choice: ",
            "devices_available": len(all_devices) > 0,
            "all_models": all_models,
            "all_devices": all_devices
        }

        print(json.dumps(result))

    except Exception as e:
        error_result = {
            "error": str(e),
            "traceback": traceback.format_exc(),
            "debug_info": {
                "nvme_input": nvme_input,
                "scsi_input": scsi_input,
                "nvme_parsed": nvme_parsed,
                "scsi_parsed": scsi_parsed
            }
        }
        print(json.dumps(error_result), file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()