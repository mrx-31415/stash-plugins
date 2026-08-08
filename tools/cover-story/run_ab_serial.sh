#!/bin/bash
# Serial runner: grey test first, then BEN2. One ONNX job at a time (the VM OOM-kills
# concurrent ones: 7.4GB RAM, onnxruntime arena defaults to 5GB+ per process).
cd /home/johan/tools/stash-plugins/tools/cover-story
echo "=== grey start $(date +%H:%M)" >> /tmp/ab_serial.log
.venv-birefnet/bin/python -u birefnet_grey_test.py >> /tmp/ab_serial.log 2>&1
echo "=== grey end $(date +%H:%M) rc=$?" >> /tmp/ab_serial.log
.venv-birefnet/bin/python -u ben2_ab.py >> /tmp/ab_serial.log 2>&1
echo "=== ben2 end $(date +%H:%M) rc=$?" >> /tmp/ab_serial.log
