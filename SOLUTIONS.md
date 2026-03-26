# 问题修复总结

## 问题1：只显示被调用函数，不显示调用者

### 修复方法
修改 `to_tree_text()` 方法，显示双向关系：
- `[Called by]`: 谁调用了这个函数（上游）
- `[Calls]`: 这个函数调用了谁（下游）

### 输出示例
```
setup (main.cpp:192)

  [Called by]
    loopTask (main.cpp:40) [EXTERNAL]

  [Calls]
      begin (HardwareSerial.cpp:262) [EXTERNAL]
      init (ControlSystem.cpp:50)
      taskControl (main.cpp:65)
```

### JSON格式
```json
{
  "index": 0,
  "self": {
    "name": "setup",
    "path": "/path/to/src/main.cpp",
    "line": [192, 265]
  },
  "parents": [16],  // 谁调用setup
  "children": [1, 2, 3]  // setup调用了谁
}
```

---

## 问题2：函数指针无法索引

### 问题描述
```cpp
xTaskCreate(taskControl, "Control", ...);
```

`taskControl` 是函数指针作为参数传入，Clangd的 `textDocument/definition` 会返回函数定义，但这是"间接关系"，不是直接调用。

### 通用性解决方案

#### 1. 配置化回调API

创建 `callback.cfg` 文件，定义哪些API接收函数指针参数：

```
# Format: api_name:param_index
xTaskCreate:0
pthread_create:1
addEventListener:1
```

**参数索引说明：**
- `xTaskCreate:0` - 第0个参数是回调函数
- `pthread_create:1` - 第1个参数是start_routine

#### 2. 自动检测和解析

工具执行以下步骤：

**步骤1：检测回调API调用**
```python
# 在函数体中查找调用
xTaskCreate(taskControl, "Control", ...)
# ^ 检测到这是一个回调API
```

**步骤2：解析回调参数**
```python
# 解析第0个参数
callback_name = "taskControl"
```

**步骤3：建立间接关系**
```
setup → xTaskCreate → taskControl
       ^           ^
       |           +-- 间接关系（通过函数指针）
       +-- 直接关系（函数调用）
```

**步骤4：递归遍历回调函数**
```
taskControl
    ├── esp_task_wdt_add
    ├── getState
    └── ...
```

#### 3. 多行调用支持

支持跨多行的API调用：

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

解析器会：
1. 累积行直到找到匹配的右括号
2. 解析完整的参数列表
3. 提取指定索引的回调参数

### 配置文件示例

#### 完整的 callback.cfg

```
# FreeRTOS APIs
xTaskCreate:0
xTaskCreateStatic:0
xTaskCreatePinnedToCore:0

# POSIX APIs
pthread_create:1
atexit:0
signal:1

# C++ Threading
std::thread::thread:0

# Arduino / ESP32
attachInterrupt:0
setTimeout:0
setInterval:0

# Common patterns
registerCallback:0
setCallback:0
addEventListener:1
on:1
once:1
off:1
```

#### 添加自定义API

假设你有自定义API：

```cpp
// Your custom API
void registerEventHandler(EventHandler handler, void* context);
```

在 `callback.cfg` 中添加：

```
registerEventHandler:0
```

### 测试结果

#### 修复前
```
setup (main.cpp:192)
    xTaskCreate (task.h:442) [EXTERNAL]  # 停在这里
```

#### 修复后
```
setup (main.cpp:192)
    taskControl (main.cpp:65)  # ✅ 回调函数
        esp_task_wdt_add (esp_task_wdt.h:83) [EXTERNAL]
        getState (ControlSystem.cpp:95)
        readTemperatureHumidity (SHT30Driver.cpp:39)
        processEvent (InputHandler.cpp:86)
    taskNetwork (main.cpp:106)
        esp_task_wdt_add (esp_task_wdt.h:83) [EXTERNAL]
        xTaskGetTickCount (task.h:1669) [EXTERNAL]
        update (NetworkManager.cpp:43)
        isConnected (NetworkManager.cpp:58)
    taskDisplay (main.cpp:146)
        esp_task_wdt_add (esp_task_wdt.h:83) [EXTERNAL]
        xTaskGetTickCount (task.h:1669) [EXTERNAL]
        log (main.cpp:36)
        getState (ControlSystem.cpp:95)
```

---

## 问题2-2：过滤范围控制

### 问题描述
超出范围的文件应该：
1. ✅ 标记为 `[EXTERNAL]`
2. ✅ 仍然显示在图中（不丢失数据）
3. ❌ **不要递归进入**（防止无限循环）

### 修复方法

在 `_build_outgoing()` 和 `_build_incoming()` 中：

```python
# 只在范围内时才递归
if self._is_in_scope(callee_path):
    self._ensure_file_opened(callee_path)
    self._build_outgoing(target_uri, callee_name, start_line_0, callee_id, current_depth + 1)
else:
    # 标记为外部，但不递归
    self._log(f"  Not recursing into external: {callee_name} @ {callee_path}")
```

### 输出示例

```bash
python main.py -p project -e "setup" -s src/ -d 2
```

```
setup (main.cpp:192)

  [Called by]
    loopTask (main.cpp:40) [EXTERNAL]

  [Calls]
      begin (HardwareSerial.cpp:262) [EXTERNAL]  # ✅ 显示
      delay (esp32-hal-misc.c:176) [EXTERNAL]  # ✅ 显示
      log (main.cpp:36)
          vsnprintf (stdio.h:389) [EXTERNAL]  # ❌ 不递归进入
          va_start (__stdarg_va_arg.h:17) [EXTERNAL]  # ❌ 不递归进入
      init (ControlSystem.cpp:50)
          reset (PIDController.cpp:68)
```

---

## 文件结构

```
clangd-call-tree/
├─ callback.cfg               # 回调API配置
├─ filter.cfg                # 文件过滤配置
├─ filter.cfg.example        # 过滤配置示例
├─ src/
│   ├─ call_graph_builder.py  # 核心逻辑（双向遍历 + 回调解析）
│   ├─ callback_config.py     # 回调配置加载
│   ├─ clangd_client.py      # LSP通信
│   ├─ cli.py               # 命令行接口
│   └─ ...
├─ README.md                # 用户文档
└─ FEATURES.md              # 功能详解
```

---

## 使用方法

### 基本用法

```bash
# 函数名模式
python main.py -p project -e "setup" -s src/

# 位置模式
python main.py -p project -e "src/main.cpp:191:5" -s src/
```

### 指定配置文件

```bash
# 自定义回调配置
python main.py -p project -e "setup" \
  --callback-config my_callbacks.cfg

# 自定义过滤配置
python main.py -p project -e "setup" \
  -c custom_filters.cfg
```

### 输出格式

```bash
# 文本格式（双向遍历）
python main.py -p project -e "setup" -f text

# JSON格式（邻接表）
python main.py -p project -e "setup" -f json -o graph.json
```

---

## 扩展指南

### 添加新的回调API

1. 编辑 `callback.cfg`
2. 添加一行：`api_name:param_index`
3. 重新运行工具

示例：
```
# 你的API
myCustomAPI:0
```

### 配置过滤规则

编辑 `filter.cfg`：

```bash
# 包含src/目录
+src/

# 排除测试文件
-test_*

# 排除第三方库
-3rdparty/
```

---

## 总结

| 问题 | 解决方案 | 通用性 |
|------|---------|--------|
| 1. 只显示被调用 | 双向遍历（parents + children） | ✅ 所有函数 |
| 2-1. 函数指针无法索引 | 配置化回调API + 自动解析 | ✅ 可扩展 |
| 2-2. 过滤范围控制 | 范围检查 + 标记EXTERNAL + 不递归 | ✅ 所有文件 |

两个问题都已解决，且具有完整的通用性和扩展性！🎉
