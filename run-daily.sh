#!/bin/bash
export DYLD_LIBRARY_PATH=/opt/homebrew/lib
cd /Users/nitishdugar/Documents/paperse-pipeline
/usr/local/bin/node run-daily.js >> ./logs/cron.log 2>&1
/usr/local/bin/node upload-daily.js >> ./logs/cron.log 2>&1
