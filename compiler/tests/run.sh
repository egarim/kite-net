#!/bin/sh
# Kite.NET compiler checks: each line is a file and the rule ids its check must report (or "ok").
cd "$(dirname "$0")/.."
fail=0
expect() {
  out=$(bin/kite check "$1" 2>&1)
  for id in $2; do
    if [ "$id" = ok ]; then echo "$out" | tail -1 | grep -q "^ok: 0 errors, 0 warnings" || { echo "FAIL $1: expected clean"; echo "$out"; fail=1; }
    else echo "$out" | grep -q " $id:" || { echo "FAIL $1: expected $id"; echo "$out"; fail=1; }; fi
  done
}
expect examples/shop.kite "ok"
expect tests/bad.kite "KT001 KT002 KT003 KT006 CS8602 CS0219"
expect tests/leak.kite "KT004"
expect tests/ca.kite "CA1829 CA1860"
bin/kite test examples/shop.kite | tail -1 | grep -q "^ok" || { echo "FAIL shop tests"; fail=1; }
# debug symbols: a runtime error names the Kite line it happened on (the PDB maps IL back through #line)
bin/kite run examples/debug/boom.kite 2>&1 | grep -q "(boom.kite:5)" || { echo "FAIL runtime error not mapped to boom.kite:5"; fail=1; }
[ $fail = 0 ] && echo "all kite checks pass"
exit $fail
