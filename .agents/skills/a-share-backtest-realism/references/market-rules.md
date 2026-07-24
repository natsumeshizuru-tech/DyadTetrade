# Market rules

Model at minimum: T+1 share availability where applicable, board/instrument lot differences, price limits, suspension, corporate actions, delisting, minimum commission, sell-side taxes, transfer fees, spread, market impact, and capacity.

Do not hardcode a timeless fee schedule. Store `effective_date`, source, broker override, and review state. A close-derived signal defaults to next-bar execution. Never enable Backtrader cheat-on-open/close without a timing proof.
