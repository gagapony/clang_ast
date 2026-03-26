# Callback Resolution & Bidirectional Traversal

## Feature 1: Bidirectional Traversal

The tool now shows both **callers** (who calls this function) and **callees** (who this function calls).

### Text Output Format

```
setup (main.cpp:192)

  [Called by]
    loopTask (main.cpp:40) [EXTERNAL]

  [Calls]
      begin (HardwareSerial.cpp:262) [EXTERNAL]
      delay (esp32-hal-misc.c:176) [EXTERNAL]
      init (ControlSystem.cpp:50)
      taskControl (main.cpp:65)
      taskNetwork (main.cpp:106)
```

### JSON Output Format

```json
{
  "index": 0,
  "self": {
    "name": "setup",
    "path": "/path/to/src/main.cpp",
    "line": [192, 265]
  },
  "parents": [16],  // Who calls setup
  "children": [1, 2, 3, 14, 15]  // Who setup calls
}
```

## Feature 2: Callback Function Resolution

The tool now detects and resolves indirect function calls through function pointers/callbacks.

### Supported Callback APIs

FreeRTOS:
- `xTaskCreate` - FreeRTOS task creation (param 0: task function)
- `xTaskCreateStatic` (param 0)
- `xTaskCreatePinnedToCore` (param 0)

POSIX:
- `pthread_create` (param 1: start_routine)
- `atexit` (param 0)
- `signal` (param 1: handler)

C++ Threading:
- `std::thread::thread` (param 0: function object)

Generic:
- `registerCallback` (param 0)
- `setCallback` (param 0)
- `addEventListener` (param 1)
- `on` (param 1)

### How It Works

When the tool detects a callback API call like:

```cpp
xTaskCreate(taskControl, "Control", ...);
```

It:
1. Identifies `xTaskCreate` as a callback API
2. Parses the first parameter (callback function pointer)
3. Resolves `taskControl` to its definition
4. Creates an edge: `setup` → `taskControl`
5. Recursively traverses into `taskControl`

### Example Output

**Before** (without callback resolution):
```
setup (main.cpp:192)
    xTaskCreate (task.h:442) [EXTERNAL]
```

**After** (with callback resolution):
```
setup (main.cpp:192)
    taskControl (main.cpp:65)
        esp_task_wdt_add (esp_task_wdt.h:83) [EXTERNAL]
        getState (ControlSystem.cpp:95)
        readTemperatureHumidity (SHT30Driver.cpp:39)
        processEvent (InputHandler.cpp:86)
    taskNetwork (main.cpp:106)
    taskDisplay (main.cpp:146)
```

## Implementation Details

### Callback API Configuration

File: `src/callback_config.py`

```python
CALLBACK_APIS = {
    "xTaskCreate": 0,  # First parameter is callback
    "pthread_create": 1,  # Second parameter is callback
    "std::thread::thread": 0,
    # ...
}
```

To add support for new callback APIs:

1. Add entry to `CALLBACK_APIS` dictionary
2. Specify the parameter index (0-based)
3. Restart the tool

### Multi-Line Call Handling

Callback resolution handles multi-line API calls:

```cpp
xTaskCreate(
    taskControl,
    "Control",
    TASK_STACK_CONTROL,
    NULL,
    TASK_PRIORITY_CONTROL,
    NULL
);
```

The parser:
1. Accumulates lines until matching closing parenthesis
2. Parses parameters from the full call text
3. Extracts the callback parameter at the specified index
4. Resolves the callback function

### Detection Logic

**Callback API Detection:**
- Regex matches: `\b([a-zA-Z_]\w*)\s*\(` (function call pattern)
- Checks against `CALLBACK_APIS` dictionary
- Marks API calls for special handling

**Function Pointer Extraction:**
- Skips the callback API itself (e.g., `xTaskCreate`)
- Parses the callback parameter (e.g., `taskControl`)
- Uses `textDocument/definition` to resolve the callback
- Creates an edge from caller to callback function

## Limitations

1. **API Coverage**: Only pre-configured callback APIs are detected
2. **Parameter Position**: Assumes fixed parameter positions (may vary by API)
3. **Complex Cases**: Lambda functions, function objects, std::function not supported
4. **Macros**: Callbacks through macros not detected

## Extending Callback Support

To add a new callback API:

```python
# In src/callback_config.py
CALLBACK_APIS["yourApiName"] = param_index
```

Example:
```python
# Your API: custom_register_handler(handler_func, context)
CALLBACK_APIS["custom_register_handler"] = 0  # First param is handler
```

## Testing

Test callback resolution:

```bash
# Analyze setup function with callbacks
python main.py -p /path/to/project -e "setup" -s src/ -d 2

# Check output for task functions (callbacks to xTaskCreate)
python main.py -p /path/to/project -e "setup" -s src/ -d 2 | grep "task"
```

Test bidirectional traversal:

```bash
# Check [Called by] section
python main.py -p /path/to/project -e "myFunction" -s src/ -d 1

# Should show callers and callees
```

## Troubleshooting

### Callback Not Detected

- Check if API is in `CALLBACK_APIS`
- Verify parameter index is correct
- Ensure callback is a function name (not expression)

### Missing Callers

- `textDocument/references` may be limited by Clangd indexing
- Some references may be in headers not indexed
- Use position mode: `-e "file.cpp:line:char"`

### Incorrect Callback Parameter

- The tool may pick wrong parameter if multiple parameters match
- Check the actual API signature
- Adjust parameter index in `CALLBACK_APIS`

## Architecture

```
CallGraphBuilder
├─ _find_calls_in_function()
│   ├─ Direct calls: func()
│   └─ Callback APIs: marked for special handling
├─ _build_outgoing()
│   ├─ Normal calls → resolve definition
│   └─ Callback APIs → _resolve_callback_parameter()
│       └─ Extracts callback function
│           └─ Creates edge: caller → callback
└─ to_tree_text()
    ├─ [Called by] ← parents
    └─ [Calls] ← children + callbacks
```
