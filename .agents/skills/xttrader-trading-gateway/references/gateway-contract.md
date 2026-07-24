# Gateway contract

Suggested order states: `created`, `validated`, `submitted`, `accepted`, `partially_filled`, `filled`, `cancel_pending`, `cancelled`, `rejected`, `expired`, `unknown`.

Persist client id, vendor id, symbol, side, type, quantity, price, filled quantity, status, timestamps, strategy tag, and redacted error. Reject illegal transitions and reconcile unknown states by query, never by resubmission. Keep synchronous/asynchronous APIs behind the same application service.
