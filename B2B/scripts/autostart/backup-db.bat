@echo off
REM Daily SQLite backup of the LinkedIn dashboard DB. Triggered by a
REM 3:15am Scheduled Task; output appended to backup-db.log.
cd /D "H:\Lead Generator\B2B"
python scripts\backup_linkedin_db.py >> "H:\Lead Generator\B2B\scripts\autostart\backup-db.log" 2>&1
