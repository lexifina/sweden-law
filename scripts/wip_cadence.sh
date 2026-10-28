#!/bin/sh
# One background process: every 14 minutes refresh the fetch line of PROGRESS.md and push wip. Stop: touch .stop-cadence
S=C:/Users/alany/Documents/lexifina/scripts/sweden-law
while [ ! -f $S/.stop-cadence ]; do
  sleep 840
  [ -f $S/.stop-cadence ] && break
  L=$(tail -1 $S/fetch.log | cut -d' ' -f1 | tr '/' '-')
  python -X utf8 - "$L" <<'PY'
import re,sys,time
p='C:/Users/alany/Documents/lexifina/scripts/sweden-law/PROGRESS.md'
s=open(p,encoding='utf-8').read()
s=re.sub(r'(?m)^- \[[ ~x]\] fetch\.py.*$', '- [~] fetch.py: list + dokumentstatus JSON + SFSR page per statute, 4 workers, gzip cache (last log: %s at %s)' % (sys.argv[1], time.strftime('%H:%M')), s)
open(p,'w',encoding='utf-8',newline='\n').write(s)
PY
  sh $S/push_wip.sh "progress $(date +%H:%M), fetch $L" >> $S/wip_cadence.log 2>&1
done
