import asyncio
import aiohttp
from aiohttp import web
import json
import subprocess
import os
import sys
import threading
import re
import importlib.util
import platform
import uuid
import shutil

# ==================================================================================
# CLOUD COMPILER SERVER (AIOHTTP) - RENDER COMPATIBLE - SECURE VERSION
# ==================================================================================

# Get API Key from Environment
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

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
        if pkg in ['os', 'sys', 'time', 'random', 'math', 'json', 'asyncio', 'threading', 'platform', 'subprocess', 're', 'aiohttp']:
            continue
        install_package(pkg, ws, loop)

async def handle_client(request):
    # --- HEALTH CHECK HANDLING ---
    if request.headers.get("Upgrade", "").lower() != "websocket":
        return web.Response(text="OK")

    # --- WEBSOCKET HANDLING ---
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    print(f"Client connected: {request.remote}")
    
    # Create unique session directory for this client
    session_id = str(uuid.uuid4())
    session_dir = os.path.join("temp_sessions", session_id)
    os.makedirs(session_dir, exist_ok=True)
    print(f"Created session directory: {session_dir}")
    
    process = None
    
    # Helper to read output stream in a separate thread
    def read_stream(stream, loop):
        try:
            while True:
                char = stream.read(1)
                if not char:
                    break
                asyncio.run_coroutine_threadsafe(
                    ws.send_json({'type': 'stdout', 'data': char}), 
                    loop
                )
            
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
                        
                        filename = os.path.join(session_dir, "temp_script.py")
                        with open(filename, "w", encoding="utf-8") as f:
                            f.write(code)
                        
                        # 🔒 SECURITY FIX: Sanitized environment (no API keys)
                        safe_env = {
                            "PYTHONIOENCODING": "utf-8",
                            "PATH": os.environ.get("PATH", ""),
                            "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
                            "HOME": os.environ.get("HOME", ""),
                            "USER": os.environ.get("USER", ""),
                            "LANG": os.environ.get("LANG", "en_US.UTF-8"),
                        }

                        process = subprocess.Popen(
                            [sys.executable, "-u", filename], 
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, 
                            text=True, 
                            bufsize=0, 
                            encoding='utf-8', 
                            env=safe_env  # ✅ Use sanitized environment
                        )
                        await ws.send_json({'type': 'status', 'msg': 'Running Python...'})

                    # --- C HANDLING ---
                    elif language == 'c':
                        filename = os.path.join(session_dir, "temp_code.c")
                        executable = os.path.join(session_dir, "a.out") if platform.system() != "Windows" else os.path.join(session_dir, "a.exe")
                        
                        with open(filename, "w", encoding="utf-8") as f:
                            f.write(code)
                        
                        await ws.send_json({'type':  'status', 'msg':  'Compiling C...'})
                        
                        # 🔒 SECURITY:  Sanitized environment
                        safe_env = {
                            "PATH": os.environ.get("PATH", ""),
                            "HOME": os.environ.get("HOME", ""),
                            "USER": os.environ.get("USER", ""),
                            "LANG": os.environ.get("LANG", "en_US.UTF-8"),
                            "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
                        }
                        
                        compile_process = subprocess.run(
                            ["gcc", filename, "-o", executable, "-lm", "-pthread"],
                            capture_output=True,
                            text=True,
                            env=safe_env
                        )
                        
                        if compile_process.returncode != 0:
                            await ws.send_json({'type': 'stdout', 'data': f"Compilation Error:\n{compile_process.stderr}"})
                            await ws.send_json({'type':  'status', 'msg':  'Compilation Failed'})
                            continue 
                        
                        await ws.send_json({'type': 'status', 'msg': 'Running C Binary...'})
                        
                        process = subprocess.Popen(
                            [executable],
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=0,
                            encoding='utf-8',
                            env=safe_env  # ✅ Sanitized environment
                        )

                    # --- C++ HANDLING ---
                    elif language == 'cpp':
                        # FIXED: Removed the space in "temp_code.cpp"
                        filename = os.path.join(session_dir, "temp_code.cpp")
                        executable = os.path.join(session_dir, "a.out") if platform.system() != "Windows" else os.path.join(session_dir, "a.exe")
                        
                        with open(filename, "w", encoding="utf-8") as f:
                            f.write(code)
                        
                        await ws.send_json({'type': 'status', 'msg': 'Compiling C++...'})
                        
                        # 🔒 SECURITY: Sanitized environment
                        safe_env = {
                            "PATH": os.environ.get("PATH", ""),
                            "HOME": os.environ.get("HOME", ""),
                            "USER": os.environ.get("USER", ""),
                            "LANG": os.environ.get("LANG", "en_US.UTF-8"),
                            "TMPDIR":  os.environ.get("TMPDIR", "/tmp"),
                        }
                        
                        compile_process = subprocess.run(
                            ["g++", filename, "-o", executable, "-lm", "-pthread"],
                            capture_output=True,
                            text=True,
                            env=safe_env
                        )
                        
                        if compile_process.returncode != 0:
                            await ws.send_json({'type': 'stdout', 'data': f"Compilation Error:\n{compile_process.stderr}"})
                            await ws.send_json({'type': 'status', 'msg': 'Compilation Failed'})
                            continue
                        
                        await ws.send_json({'type': 'status', 'msg': 'Running C++ Binary...'})
                        
                        process = subprocess.Popen(
                            [executable],
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=0,
                            encoding='utf-8',
                            env=safe_env  # ✅ Sanitized environment
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

                # --- AI FIX HANDLER ---
                elif data.get('type') == 'ai_fix':
                    code = data.get('code')
                    error_log = data.get('error')
                    language = data.get('language', 'c')
                    
                    if not GEMINI_API_KEY:  
                        await ws.send_json({'type': 'ai_error', 'msg':  'Server Error:  GEMINI_API_KEY not configured.'})
                        continue

                    # Construct Prompt - Ask for JSON in plain text
                    prompt = f"""You are an expert {language.upper()} debugger. Analyze this code and error, then respond with ONLY a valid JSON object (no markdown, no backticks).

CODE:  
{code}

ERROR: 
{error_log}

Respond in this exact JSON format:
{{
    "explanation": "Brief 1-2 sentence explanation of the bug",
    "fixed_code":  "Complete corrected code here"
}}"""

                    try:
                        # ✅ FIXED: Use correct stable model endpoint
                        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={GEMINI_API_KEY}"
                        
                        print(f"[AI] Calling Gemini API...")
                        
                        async with aiohttp.ClientSession() as session:
                            async with session.post(
                                api_url,
                                headers={"Content-Type":  "application/json"},
                                json={
                                    "contents": [{"parts": [{"text": prompt}]}],
                                    "generationConfig": {
                                        "temperature": 0.1,
                                        "maxOutputTokens": 2048
                                    }
                                }
                            ) as resp:
                                response_text = await resp.text()
                                
                                print(f"[AI] Status: {resp.status}")
                                print(f"[AI] Response preview: {response_text[: 200]}")
                                
                                if resp.status != 200:
                                    await ws.send_json({
                                        'type': 'ai_error', 
                                        'msg': f"AI API Error (Status {resp.status})"
                                    })
                                else:
                                    try:
                                        result = json.loads(response_text)
                                        
                                        # Extract AI response
                                        ai_text = result['candidates'][0]['content']['parts'][0]['text']
                                        
                                        # Clean markdown if present
                                        ai_text = ai_text.strip()
                                        if ai_text.startswith('```json'):
                                            ai_text = ai_text.split('```json')[1].split('```')[0].strip()
                                        elif ai_text.startswith('```'):
                                            ai_text = ai_text.split('```')[1].split('```')[0].strip()
                                        
                                        # Parse JSON
                                        parsed_response = json.loads(ai_text)
                                        
                                        # Validate structure
                                        if 'explanation' in parsed_response and 'fixed_code' in parsed_response: 
                                            print(f"[AI] Success! Sending response to client")
                                            await ws.send_json({
                                                'type': 'ai_response', 
                                                'data': parsed_response
                                            })
                                        else:
                                            print(f"[AI] Invalid structure:  {parsed_response}")
                                            await ws.send_json({
                                                'type': 'ai_error', 
                                                'msg': 'Invalid AI response format'
                                            })
                                            
                                    except (KeyError, IndexError) as e:
                                        print(f"[AI] API structure error: {e}")
                                        await ws.send_json({
                                            'type': 'ai_error', 
                                            'msg': 'Unexpected API response'
                                        })
                                    except json.JSONDecodeError as e:
                                        print(f"[AI] JSON parse error: {e}")
                                        print(f"[AI] Attempted to parse:  {ai_text[: 300]}")
                                        await ws.send_json({
                                            'type': 'ai_error', 
                                            'msg': 'AI returned invalid JSON'
                                        })
                                    
                    except aiohttp.ClientError as e:
                        print(f"[AI] Network error: {e}")
                        await ws.send_json({
                            'type': 'ai_error', 
                            'msg': 'Network error'
                        })
                    except Exception as e:  
                        print(f"[AI] Unexpected error: {e}")
                        import traceback
                        traceback.print_exc()
                        await ws.send_json({
                            'type': 'ai_error', 
                            'msg': f'Server error: {type(e).__name__}'
                        })

            elif msg.type == web.WSMsgType.ERROR:
                print(f'ws connection closed with exception {ws.exception()}')

    finally:
        if process:  
            try:
                process.kill()
            except:
                pass
        # Clean up session directory
        try:
            shutil.rmtree(session_dir, ignore_errors=True)
            print(f"Cleaned up session directory: {session_dir}")
        except:
            pass
        print("Client disconnected")

    return ws

async def main():
    # Create temp_sessions directory if it doesn't exist
    os.makedirs("temp_sessions", exist_ok=True)
    print("temp_sessions directory ready")
    
    port = int(os.environ.get("PORT", 8765))
    app = web.Application()
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
