# 本机运行指南：用 IBKR 采集期权快照

H7 需要在每个财报事件的建仓收盘时点，记录一次期权链快照。IBKR API 不提供已到期期权的历史数据，所以只能从现在开始向前采集。采集必须在能连上 TWS 或 IB Gateway 的电脑上运行。

> 这部分代码无法在云端会话里测试，因为那里连不到 IBKR。第一次运行时请留意报错。

## 1. 安装

以下命令以 Windows 的 PowerShell 为例，项目放在 `E:\Coding\Quant\estimate`。Linux 或 macOS 把 `.venv\Scripts\python` 换成 `.venv/bin/python` 即可。

```powershell
cd E:\Coding\Quant
git clone https://github.com/zx2592/estimate
cd estimate
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[ibkr,dev]"
.venv\Scripts\python -m pytest -q tests
```

第一次还需要把历史数据拉到本地，大约 5 分钟：

```powershell
.venv\Scripts\python -m estimate.cli events
.venv\Scripts\python -m estimate.cli fetch-poly
.venv\Scripts\python -m estimate.cli panel
.venv\Scripts\python -m estimate.cli fetch-prices
```

## 2. TWS 或 IB Gateway 设置

1. 登录 TWS 或 IB Gateway。模拟账户也可以，前提是在账户设置里开启了与模拟账户共享行情订阅。
2. 打开 API 设置。在 TWS 里是 Configure → API → Settings。
   - 勾选 Enable ActiveX and Socket Clients。
   - 勾选 Read-Only API。代码只读数据，不下单。
   - 记下 Socket port。TWS 模拟账户 7497，实盘 7496。Gateway 模拟账户 4002，实盘 4001。
   - Trusted IPs 里加上 127.0.0.1。
3. 行情订阅：
   - **推荐订阅美国期权实时行情（OPRA）。** 在 Client Portal 的行情订阅页面开通，个人用户的月费较低，以页面显示为准。运行时加 `--market-data-type 1`。
   - **不订阅也能用延迟行情，这是默认设置。** 延迟大约 15 分钟，所以要在收盘后再跑，见下面的时间表。

## 3. 每日采集

| 美东时间 | 命令 | 作用 |
|---|---|---|
| 15:30 | `upcoming` | 刷新未来 10 天有 EPS 合约的财报事件 |
| 15:45，实时行情 | `snapshot-options --market-data-type 1` | 对今天收盘建仓的事件拍期权链快照 |
| 16:10，延迟行情 | `snapshot-options --market-data-type 4` | 同上，用延迟冻结行情拿到接近收盘的报价 |

先在仓库根目录建一个日志目录：`mkdir -p logs`。Windows 用任务计划程序。任务的触发时间按你电脑的本地时区填写，要换算成美东 15:30 和 15:45。下面是 crontab 示例，适用于 Linux。CRON_TZ 在 Linux 上可用。macOS 自带的 cron 不支持它，需要换算成本地时间，或者改用 launchd。

```
CRON_TZ=America/New_York
30 15 * * 1-5 cd ~/estimate && .venv/bin/python -m estimate.cli upcoming >> logs/upcoming.log 2>&1
45 15 * * 1-5 cd ~/estimate && .venv/bin/python -m estimate.cli snapshot-options --port 7497 --market-data-type 1 >> logs/snapshot.log 2>&1
```

快照存放在 `data/options/snapshots/<建仓日>/<代码>.parquet`。

**没有 IBKR 的免费备选。** 加上 `--source yahoo` 就改从 Yahoo 拉期权链。隐含波动率和 delta 用买卖中间价重新计算，不用 Yahoo 自带的数值。Yahoo 对云服务器 IP 限流，但在家用网络上通常能用。

## 4. 每周评估

周末运行一次，把新结算的合约和新行情加进来，再看 H7 的累计结果：

```bash
.venv/bin/python -m estimate.cli events
.venv/bin/python -m estimate.cli fetch-poly
.venv/bin/python -m estimate.cli panel
.venv/bin/python -m estimate.cli fetch-prices
.venv/bin/python -m estimate.cli h7
```

## 5. H5：用历史隐含波动率检验

IBKR 能提供每只股票过去两年的 30 天隐含波动率日线。这份数据不需要期权行情订阅，可以回看过去所有财报事件。TWS 保持登录，然后运行：

```powershell
.venv\Scripts\python -m estimate.cli ibkr-vol-history --port 7497
.venv\Scripts\python -m estimate.cli h5
```

- 第一条命令会下载约 425 只股票的数据，存到 `data/ibkr/vol/`，大约需要 20 到 40 分钟。中断后重跑会跳过已下载的股票。
- 用 IB Gateway 的话，把端口换成 4002，实盘账户是 4001。
- 没有上市期权的股票会显示 no data，这是正常的。
