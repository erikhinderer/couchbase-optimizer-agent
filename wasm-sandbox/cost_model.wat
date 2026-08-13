;; Built-in cost estimator used by /simulate/query-plan-cost. No imports, no
;; memory, no host access -- pure integer arithmetic over the wasmtime store's
;; fuel budget. Models the relative I/O cost of a query plan as
;; (documents actually scanned) * (average document size), which is enough to
;; rank "primary/full scan" vs "index-narrowed" plans against each other. This
;; is a coarse what-if signal, not a substitute for the query planner's own
;; cost-based optimizer -- it exists to sanity-check that a suggested index
;; actually reduces estimated scan volume before the finding is surfaced with
;; a "tested in sandbox" badge.
(module
  (func $estimate_cost (export "estimate_cost")
    (param $doc_count i64)
    (param $avg_doc_size i64)
    (param $selectivity_permille i64)
    (param $uses_index i32)
    (result i64)
    (local $scanned i64)

    (if (i32.eqz (local.get $uses_index))
      (then
        (local.set $scanned (local.get $doc_count)))
      (else
        (local.set $scanned
          (i64.div_u
            (i64.mul (local.get $doc_count) (local.get $selectivity_permille))
            (i64.const 1000)))))

    (i64.div_u
      (i64.mul (local.get $scanned) (local.get $avg_doc_size))
      (i64.const 1024))
  )
)
