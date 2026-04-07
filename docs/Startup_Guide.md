# 启动指南

## 启动 Server 和 Client

### Windows

使用 PowerShell 启动（会打开新的命令行窗口）：

**启动 Server:**
```powershell
Start-Process cmd -ArgumentList '/c cd D:\MyProject\Programming\Open-AutoGLM\Distributed\Server && python -m uvicorn src.main:app --host 0.0.0.0 --port 8080' -WindowStyle Normal
```

**启动 Client:**
```powershell
Start-Process cmd -ArgumentList '/c cd D:\MyProject\Programming\Open-AutoGLM\Distributed\Client && python main.py --server ws://localhost:8080 --log-level INFO' -WindowStyle Normal
```

### 手动启动

**Server:**
```bash
cd D:\MyProject\Programming\Open-AutoGLM\Distributed\Server
python -m uvicorn src.main:app --host 0.0.0.0 --port 8080
```

**Client:**
```bash
cd D:\MyProject\Programming\Open-AutoGLM\Distributed\Client
python main.py --server ws://localhost:8080 --log-level INFO
```

## 停止进程

查找进程：
```bash
wmic process where "name like '%python%'" get processid,commandline
```

停止进程：
```bash
taskkill /F /PID <ProcessId>
```

## Web UI

启动 Web UI：
```bash
cd D:\MyProject\Programming\Open-AutoGLM\Distributed\Web
npm run dev
```

访问 http://localhost:5173 或 http://localhost:3000
