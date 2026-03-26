#!/bin/bash
# Fast test script for smart-drying-module

PROJECT="/home/gabriel/.openclaw/code/projects/smart-drying-module"
OUTPUT_DIR="/tmp/smart-drying-simplified"

# Create simplified project structure
mkdir -p "$OUTPUT_DIR/src"
mkdir -p "$OUTPUT_DIR/.cache"

# Copy only main.cpp and related files
cp "$PROJECT/src/main.cpp" "$OUTPUT_DIR/src/"
cp "$PROJECT/src/I2CDriver.cpp" "$OUTPUT_DIR/src/" 2>/dev/null
cp "$PROJECT/src/I2CDriver.h" "$OUTPUT_DIR/src/" 2>/dev/null

# Create minimal compile_commands.json
python3 << 'PYTHON'
import json

with open('/home/gabriel/.openclaw/code/projects/smart-drying-module/compile_commands.json') as f:
    all_entries = json.load(f)

# Only include main.cpp
main_entries = [e for e in all_entries if 'main.cpp' in e['file']]

# Fix paths
for entry in main_entries:
    entry['file'] = entry['file'].replace(
        '/home/gabriel/.openclaw/code/projects/smart-drying-module',
        '/tmp/smart-drying-simplified'
    )
    entry['directory'] = '/tmp/smart-drying-simplified'

with open('/tmp/smart-drying-simplified/compile_commands.json', 'w') as f:
    json.dump(main_entries, f, indent=2)

print(f"Created simplified compile_commands.json with {len(main_entries)} entries")
PYTHON

echo "Simplified project ready at: $OUTPUT_DIR"
echo "Run: python3 main.py -p $OUTPUT_DIR -f setup -c filter.cfg.example"
