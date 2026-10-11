# dev-tool

> ⚠️ **本仓库所有脚本只保证在 Python 3.14 及以上版本能跑；更低版本不做任何保证。**

个人自用的 EU4 mod 开发工具集合：把平时自己写（或让 AI 写）的脚本攒在一起，用于做 mod。不追求通用性，按需增删。

- **仓库根目录**：通用工具，一件工具解决一件完整的事。
- **[`mod_scripts/`](mod_scripts/README.md)**：做 mod 用的具体游戏数据脚本，例如修改某国的基础发展度。

脚本只用 Python 标准库，不需要虚拟环境，也不需要安装第三方依赖。仓库内的脚本都是**单文件**，可以单独复制出去运行，不依赖本仓库的其他文件；`init_mod_project.py` 的用途绑定在这个仓库上（它负责把仓库挂进 mod 项目），所以从仓库里运行。

## 目录结构

```
dev-tool/
├── init_mod_project.py          # 初始化 mod 项目：建立 dev-tools 链接、写 .gitignore、git init
├── escape_eu4_special_chars.py  # 文本转义 + 本地化文件批量转码（C++ 版 EU4SpecialEscape 的 Python 复刻）
├── mod_scripts/                 # 做 mod 用的游戏数据脚本
├── AGENTS.md                    # 脚本编写规范
├── README.md
└── ruff.toml
```

> `escape_eu4_special_chars.py` 复刻自 [matanki-saito/EU4SpecialEscape](https://github.com/matanki-saito/EU4SpecialEscape)（MIT，Copyright (c) 2018 matanki-saito），原始版权与许可声明全文保留在该文件头部的注释里。

## 用法

> 下面都是**编造示例**，请依据实际情况选择脚本进行调用。

```pwsh
# 1. 把本仓库挂进 mod 项目：生成 dev-tools 链接 + .gitignore 条目 + git init
python init_mod_project.py --tools-dir D:\example\dev-tool D:\example\my-mod

# 2. 之后在那个项目里，通过 dev-tools 链接调用脚本
python dev-tools\mod_scripts\development_boost.py --source-root D:\example\eu4 --owner MNG --dry-run
```

每个脚本的完整说明（用途、模式、安全默认、示例）见 `python <脚本> --help`。
