import asyncio
from aiohttp import web
import json
import subprocess
import os
import sys
import threading
import re
import importlib.util
import platform  # <--- FIXED: Added missing import

# ==================================================================================
# CLOUD COMPILER SERVER (AIOHTTP) - RENDER COMPATIBLE
# ==================================================================================

def install_package(package_name, ws=None, loop=None):
    """Attempt to install a python package via pip."""
    try:
        if importlib.util.find_spec(package_name) is not None:
            return
        
        msg = f"Installing missing package: {package_name}..."
        print(msg)
        if ws and loop:
             asyncio.run_coroutine_threadsafe(
                ws.send_json({'type': 'status', 'msg': msg}), 
                loop
            )

        subprocess.check_call([sys.executable, "-m", "pip", "install", package_name])
    except Exception as e:
        error_msg = f"Failed to install {package_name}: {e}"
        print(error_msg)
        if ws and loop:
             asyncio.run_coroutine_threadsafe(
                ws.send_json({'type': 'stdout', 'data': f"\n[Error] {error_msg}\n"}), 
                loop
            )

def check_and_install_packages(code, ws=None, loop=None):
    """Scan Python code for imports and install them if missing."""
    imports = re.findall(r'^\s*import\s+(\w+)', code, re.MULTILINE)
    from_imports = re.findall(r'^\s*from\s+(\w+)', code, re.MULTILINE)
    unique_packages = set(imports + from_imports)
    for pkg in unique_packages:
        if pkg in ['os', 'sys', 'time', 'random', 'math', 'json', 'asyncio', 'threading', 'platform', 'subprocess', 're']:
            continue
        install_package(pkg, ws, loop)

async def handle_client(request):
    # --- HEALTH CHECK HANDLING ---
    # Render sends HEAD/GET to root. If not a websocket upgrade, return OK.
    if request.headers.get("Upgrade", "").lower() != "websocket":
        return web.Response(text="OK")

    # --- WEBSOCKET HANDLING ---
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    print(f"Client connected: {request.remote}")
    process = None
    
    # Helper to read output stream in a separate thread
    def read_stream(stream, loop):
        try:
            while True:
                # Read 1 byte/char at a time
                char = stream.read(1)
                if not char:
                    break
                # aiohttp's send_json is a coroutine
                asyncio.run_coroutine_threadsafe(
                    ws.send_json({'type': 'stdout', 'data': char}), 
                    loop
                )
            
            # --- SIGNAL FINISH ---
            # When stream ends (process exits), tell frontend
            asyncio.run_coroutine_threadsafe(
                ws.send_json({'type': 'status', 'msg': 'Program finished'}), 
                loop
            )

        except Exception:
            pass

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                data = json.loads(msg.data)
                
                if data.get('type') == 'run':
                    code = data.get('code')
                    language = data.get('language', 'python')
                    
                    # --- PYTHON HANDLING ---
                    if language == 'python':
                        await ws.send_json({'type': 'status', 'msg': 'Checking dependencies...'})
                        loop = asyncio.get_running_loop()
                        await loop.run_in_executor(None, check_and_install_packages, code, ws, loop)
                        
                        filename = "temp_script.py"
                        with open(filename, "w", encoding="utf-8") as f:
                            f.write(code)
                        
                        env = os.environ.copy()
                        env["PYTHONIOENCODING"] = "utf-8"

                        process = subprocess.Popen(
                            [sys.executable, "-u", filename], 
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, 
                            text=True, 
                            bufsize=0, 
                            encoding='utf-8', 
                            env=env
                        )
                        await ws.send_json({'type': 'status', 'msg': 'Running Python...'})

                    # --- C HANDLING ---
                    elif language == 'c':
                        filename = "temp_code.c"
                        executable = "./a.out" if platform.system() != "Windows" else "a.exe"
                        
                        with open(filename, "w", encoding="utf-8") as f:
                            f.write(code)
                        
                        await ws.send_json({'type': 'status', 'msg': 'Compiling C...'})
                        
                        compile_process = subprocess.run(
                            ["gcc", filename, "-o", executable],
                            capture_output=True,
                            text=True
                        )
                        
                        if compile_process.returncode != 0:
                            await ws.send_json({'type': 'stdout', 'data': f"Compilation Error:\n{compile_process.stderr}"})
                            continue 
                        
                        await ws.send_json({'type': 'status', 'msg': 'Running C Binary...'})
                        
                        process = subprocess.Popen(
                            [executable],
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=0,
                            encoding='utf-8'
                        )

                    # --- C++ HANDLING ---
                    elif language == 'cpp':
                        filename = "temp_code.cpp"
                        executable = "./a.out" if platform.system() != "Windows" else "a.exe"
                        
                        with open(filename, "w", encoding="utf-8") as f:
                            f.write(code)
                        
                        await ws.send_json({'type': 'status', 'msg': 'Compiling C++...'})
                        
                        compile_process = subprocess.run(
                            ["g++", filename, "-o", executable],
                            capture_output=True,
                            text=True
                        )
                        
                        if compile_process.returncode != 0:
                            await ws.send_json({'type': 'stdout', 'data': f"Compilation Error:\n{compile_process.stderr}"})
                            continue
                        
                        await ws.send_json({'type': 'status', 'msg': 'Running C++ Binary...'})
                        
                        process = subprocess.Popen(
                            [executable],
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=0,
                            encoding='utf-8'
                        )

                    if process:
                        loop = asyncio.get_running_loop()
                        thread = threading.Thread(target=read_stream, args=(process.stdout, loop))
                        thread.daemon = True 
                        thread.start()

                elif data.get('type') == 'input':
                    if process and process.poll() is None:
                        user_input = data.get('data')
                        try:
                            process.stdin.write(user_input)
                            process.stdin.flush()
                        except Exception:
                            pass
            
            elif msg.type == web.WSMsgType.ERROR:
                print(f'ws connection closed with exception {ws.exception()}')

    finally:
        if process: process.kill()
        print("Client disconnected")

    return ws

async def main():
    port = int(os.environ.get("PORT", 8765))
    app = web.Application()
    # Route root path to the unified handler (handles both HTTP checks and WS upgrades)
    app.add_routes([web.get('/', handle_client)])
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    print(f"AIOHTTP Server started on port {port}...")
    await site.start()
    
    # Keep the server running
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())