import psutil
import os
import time
from logger import log

def get_system_metrics():
    """
    CPU, RAM နှင့် Disk Usage အချက်အလက်များကို ရယူသည်
    """
    try:
        # CPU Usage (1 second interval for accuracy)
        cpu_usage = psutil.cpu_percent(interval=0.5)
        
        # RAM Usage
        memory = psutil.virtual_memory()
        ram_total = round(memory.total / (1024**3), 2)
        ram_used = round(memory.used / (1024**3), 2)
        ram_percent = memory.percent
        
        # Disk Usage
        disk = psutil.disk_usage('/')
        disk_total = round(disk.total / (1024**3), 2)
        disk_used = round(disk.used / (1024**3), 2)
        disk_percent = disk.percent
        
        # Process Info (Current Bot Process)
        process = psutil.Process(os.getpid())
        bot_ram = round(process.memory_info().rss / (1024**2), 2) # MB
        
        return {
            "cpu": cpu_usage,
            "ram_total": ram_total,
            "ram_used": ram_used,
            "ram_percent": ram_percent,
            "disk_total": disk_total,
            "disk_used": disk_used,
            "disk_percent": disk_percent,
            "bot_ram": bot_ram,
            "uptime": get_uptime()
        }
    except Exception as e:
        log.error(f"Error gathering system metrics: {e}")
        return None

def get_uptime():
    """ System Uptime ကို တွက်ချက်သည် """
    try:
        boot_time = psutil.boot_time()
        uptime_seconds = time.time() - boot_time
        
        days = int(uptime_seconds // (24 * 3600))
        hours = int((uptime_seconds % (24 * 3600)) // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        
        if days > 0:
            return f"{days}d {hours}h {minutes}m"
        else:
            return f"{hours}h {minutes}m"
    except:
        return "Unknown"
