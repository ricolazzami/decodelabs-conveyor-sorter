# FUXA ships without the Modbus driver dependency, so the stock image cannot
# talk to the PLC ("try to create PLC but plugin is missing!").
# This thin layer only adds modbus-serial; FUXA itself is untouched.
FROM frangoteam/fuxa:latest

RUN cd /usr/src/app/FUXA/server \
 && npm install --no-save --no-audit --no-fund modbus-serial@8