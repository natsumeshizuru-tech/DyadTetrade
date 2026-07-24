# Timing model

| Event | Available information | Earliest safe action |
|---|---|---|
| bar opens | prior completed bars, current open | open decision if designed ex ante |
| intrabar tick | partial current bar | intrabar rule only |
| bar closes | completed OHLCV | next eligible event/bar |
| historical replay | completed historical bar | simulated next-bar execution |

Never let a current bar's high, low, close, or volume influence an assumed fill earlier in that bar.
