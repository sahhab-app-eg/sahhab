#!/bin/sh
# POT token provider (127.0.0.1:4416) next to the app; the site keeps working
# for other sites even if this exits.
node /home/user/bgutil/server/build/main.js >/tmp/pot-server.log 2>&1 &
exec python app.py
