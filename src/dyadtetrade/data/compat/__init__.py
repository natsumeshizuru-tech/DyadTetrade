from __future__ import annotations

from dyadtetrade.data.compat.xtdata_compat import LocalXtData, configure_local_xtdata

local_xtdata = LocalXtData()

__all__ = ["LocalXtData", "configure_local_xtdata", "local_xtdata"]
