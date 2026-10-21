"""Initialize local configuration without displaying credentials. Standard library only."""

import re
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
target = root / ".env"
source = target if target.exists() else root.parent / ".env"
original = source.read_text() if source.exists() else ""
values = {}
for line in original.splitlines():
    match = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
    if match:
        values[match[1]] = match[2]
lines = original.rstrip().splitlines()
for line in (root / ".env.example").read_text().splitlines():
    if "=" not in line or line.startswith("#"):
        continue
    name, value = line.split("=", 1)
    if name not in values:
        lines.append(line)
        values[name] = value
for name in ("AUTH_OWNER_SECRET", "AUTH_CLIENT_SECRET"):
    if not values.get(name, "").strip("\"'"):
        lines = [line for line in lines if not re.match(rf"\s*{name}\s*=", line)]
        lines.append(name + "=" + secrets.token_urlsafe(32))
target.write_text("\n".join(lines) + "\n")
target.chmod(0o600)
print("Configuration prepared in .env; login secrets generated only if absent. Values were not displayed.")
