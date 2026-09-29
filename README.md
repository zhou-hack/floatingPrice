# Floating Ticker

一个基于 PyQt6 的桌面悬浮行情组件，默认显示 `ETH/USDT`，实时连接交易所 WebSocket 获取价格、24 小时涨跌、高点和低点。

## 预览

### 浅色模式

![Floating Ticker light mode](docs/light.png)

### 深色模式

![Floating Ticker dark mode](docs/dark.png)

## 特性

- 无边框、可拖动、可置顶的桌面悬浮窗
- 默认 `ETH/USDT`，支持 BTC、SOL、BNB、XRP 等常用交易对
- 支持 Binance、OKX、Bybit、Gate.io、Bitget
- WebSocket 实时价格更新，断线自动重连
- 价格变化时绿色/红色闪烁提示
- 价格和涨跌幅使用固定列宽，更新时不会抖动或缩放
- 数据源 Logo 和币种 Logo
- Logo 大小可分别按 px 调整
- BTC/ETH 基础币种文字字号可单独调整，和 `/USDT` 保持对齐
- 浅色、深色、跟随系统三种主题
- HTTP / SOCKS5 代理配置
- 右键菜单切换数据源、交易对、主题、代理和退出
- 关闭后保存窗口位置、交易对、数据源、主题和 Logo 设置

## 安装

需要 Python 3.10 或更高版本。

```bash
pip install -r requirements.txt
```

## 运行

```bash
python main.py
```

## 操作

- 左键拖动窗口
- 右键打开菜单
- `数据源`：切换 Binance、OKX、Bybit、Gate.io、Bitget
- `切换交易对`：选择常用交易对或输入自定义交易对
- `外观`：跟随系统、深色、浅色
- `Logo 大小`：分别设置币种 Logo、平台 Logo 和基础币种文字字号，范围 `8–32px`
- `代理设置`：配置 HTTP 或 SOCKS5 代理
- `窗口置顶`：切换置顶状态
- `退出`：关闭程序

## 代理

也可以使用环境变量：

```powershell
$env:HTTPS_PROXY = "http://127.0.0.1:7890"
python main.py
```

支持 HTTP、SOCKS5，以及带认证的代理地址：

```text
http://user:password@127.0.0.1:7890
socks5://127.0.0.1:1080
```

## 项目结构

```text
.
├── main.py              # PyQt6 应用程序
├── requirements.txt     # Python 依赖
├── assets/              # 币种 SVG Logo
├── ico/                 # 交易所 Logo
└── docs/                # README 截图
```

## 数据来源

行情来自各交易所公开 WebSocket，不需要 API Key。不同交易所的订阅协议和字段格式不同，程序内部分别处理。

## License

暂未指定 License。
