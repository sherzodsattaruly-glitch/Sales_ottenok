import paramiko
import sys

password = 'tR5SCP&WoP3n'

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('45.139.29.234', username='root', password=password)

# Search for tracebacks and errors in the logs
cmds = [
    # Look for Traceback in stderr log
    "grep -i 'traceback\|Error\|ImportError\|ModuleNotFoundError\|SyntaxError\|Exception' /var/log/sales_ottenok/stderr.log 2>&1 | tail -100",
    # Check log around 07:29-07:31 crash start
    "awk '/07:28|07:29|07:30/{found=1} found{print; if (++c>=100) exit}' /var/log/sales_ottenok/stderr.log 2>&1",
]

for cmd in cmds:
    stdin, stdout, stderr = client.exec_command(cmd)
    out = stdout.read()
    label = cmd[:60]
    sys.stdout.buffer.write(f"\n=== {label} ===\n".encode())
    sys.stdout.buffer.write(out)
    sys.stdout.buffer.write(b"\n")

client.close()
