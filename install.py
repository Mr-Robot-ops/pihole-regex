import os
import sqlite3
import subprocess
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def fetch_url(url):

    if not url:
        return

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:71.0) Gecko/20100101 Firefox/71.0'}

    print('[i] Fetching:', url)

    try:
        response = urlopen(Request(url, headers=headers))
    except HTTPError as e:
        print('[E] HTTP Error:', e.code, 'whilst fetching', url)
        return
    except URLError as e:
        print('[E] URL Error:', e.reason, 'whilst fetching', url)
        return

    # Read and decode
    response = response.read().decode('UTF-8').replace('\r\n', '\n')

    # If there is data
    if response:
        # Strip leading and trailing whitespace
        response = '\n'.join(x.strip() for x in response.splitlines())

    # Return the hosts
    return response

#Thank you mmotti and CyberAndi
url_regexps_remote = 'https://github.com/Mr-Robot-ops/pihole-regex'
path_pihole = r'/etc/pihole'
path_legacy_regex = os.path.join(path_pihole, 'regex.list')
path_legacy_mrrobotops_regex = os.path.join(path_pihole, 'mrrobotops-regex.list')
path_pihole_db = os.path.join(path_pihole, 'gravity.db')
install_comment = 'github.com/Mr-Robot-ops/pihole-regex'


db_exists = False
conn = None
c = None

regexps_remote = set()
regexps_local = set()
regexps_mrrobotops_local = set()
regexps_legacy = set()
regexps_remove = set()

# Exit if not running as root
if not os.getuid() == 0:
    print('Please run this script as root')
    exit(1)
else:
    print('[i] Root user detected')

# Exit if Pi-hole dir does not exist
if not os.path.exists(path_pihole):
    print(f'{path_pihole} was not found')
    exit(1)
else:
    print('[i] Pi-hole path exists')

# Determine whether we are using DB or not
if os.path.isfile(path_pihole_db) and os.path.getsize(path_pihole_db) > 0:
    db_exists = True
    print('[i] DB detected')
else:
    print('[i] Legacy regex.list detected')

# Fetch the remote regexps
str_regexps_remote = fetch_url(url_regexps_remote)

# If regexps were fetched, remove any comments and add to set
if str_regexps_remote:
    regexps_remote.update(x for x in str_regexps_remote.splitlines() if x and x[:1] != '#')
    print(f'[i] {len(regexps_remote)} regexps collected from {url_regexps_remote}')
else:
    print('[i] No remote regexps were found.')
    exit(1)

if db_exists:
    # Create a DB connection
    print(f'[i] Connecting to {path_pihole_db}')

    try:
        conn = sqlite3.connect(path_pihole_db)
    except sqlite3.Error as e:
        print(e)
        exit(1)

    # Create a cursor object
    c = conn.cursor()

    # Add / update remote regexps
    print('[i] Adding / updating regexps in the DB')

    # Check for SQLite version
    sqlite3_major, sqlite3_minor, sqlite3_revision = sqlite3.sqlite_version_info

    # If SQLite version < 3.24 (required for UPSERT)
    if sqlite3_major == 3 and sqlite3_minor < 24:
        c.executemany('INSERT OR IGNORE INTO domainlist (type, domain, enabled, comment) '
                      'VALUES (3, ?, 1, ?)',
                      [(x, install_comment) for x in sorted(regexps_remote)])
        c.executemany('UPDATE domainlist '
                      'SET comment = ? WHERE domain in (?) AND comment != ?',
                      [(install_comment, x, install_comment) for x in sorted(regexps_remote)])
    else:
        c.executemany('INSERT INTO domainlist (type, domain, enabled, comment) '
                      'VALUES (3, ?, 1, ?) '
                      'ON CONFLICT(domain) DO UPDATE SET '
                      'comment = excluded.comment '
                      'WHERE comment != excluded.comment',
                      [(x, install_comment) for x in sorted(regexps_remote)])
    conn.commit()

    # Fetch all current mrrobotops regexps in the local db
    c.execute('SELECT domain FROM domainlist WHERE type = 3 AND comment = ?', (install_comment,))
    regexps_mrrobotops_local_results = c.fetchall()
    regexps_mrrobotops_local.update([x[0] for x in regexps_mrrobotops_local_results])

    # Remove any local entries that do not exist in the remote list
    # (will only work for previous installs where we've set the comment field)
    print('[i] Identifying obsolete regexps')
    regexps_remove = regexps_mrrobotops_local.difference(regexps_remote)

    if regexps_remove:
        print('[i] Removing obsolete regexps')
        c.executemany('DELETE FROM domainlist WHERE type = 3 AND domain in (?)', [(x,) for x in sorted(regexps_remove)])
        conn.commit()

    # Delete Mr-Robot-ops regex.list as if we've migrated to the db, it's no longer needed
    if os.path.exists(path_legacy_mr_robot_ops_regex):
        os.remove(path_legacy_mr_robot_ops_regex)

    print('[i] Restarting Pi-hole')
    subprocess.call(['pihole', 'restartdns', 'reload'], stdout=subprocess.DEVNULL)

    # Prepare final result
    print('[i] Done - Please see your installed regexps below\n')

    c.execute('Select domain FROM domainlist WHERE type = 3')
    final_results = c.fetchall()
    regexps_local.update(x[0] for x in final_results)

    print(*sorted(regexps_local), sep='\n')

    conn.close()

else:
    if os.path.isfile(path_legacy_regex) and os.path.getsize(path_legacy_regex) > 0:
        print('[i] Collecting existing entries from regex.list')
        with open(path_legacy_regex, 'r') as fRead:
            regexps_local.update(x for x in (x.strip() for x in fRead) if x and x[:1] != '#')

    if regexps_local:
        print(f'[i] {len(regexps_local)} existing regexps identified')
        # If we have a record of the previous install remove the install items from the set
        if os.path.isfile(path_legacy_mr_robot_ops_regex) and os.path.getsize(path_legacy_regex) > 0:
            print('[i] Existing mr-robot-ops-regex install identified')
            with open(path_legacy_mr_robot_ops_regex, 'r') as fOpen:
                regexps_legacy.update(x for x in (x.strip() for x in fOpen) if x and x[:1] != '#')

                if regexps_legacy:
                    print('[i] Removing previously installed regexps')
                    regexps_local.difference_update(regexps_legacy)

    # Add remote regexps to local regexps
    print(f'[i] Syncing with {url_regexps_remote}')
    regexps_local.update(regexps_remote)

    # Output to regex.list
    print(f'[i] Outputting {len(regexps_local)} regexps to {path_legacy_regex}')
    with open(path_legacy_regex, 'w') as fWrite:
        for line in sorted(regexps_local):
            fWrite.write(f'{line}\n')

    # Output Mr-Robot-ops remote regexps to Mr-Robot-ops-regex.list
    # for future install / uninstall
    with open(path_legacy_mr_robot_ops_regex, 'w') as fWrite:
        for line in sorted(regexps_remote):
            fWrite.write(f'{line}\n')

    print('[i] Restarting Pi-hole')
    subprocess.call(['pihole', 'restartdns', 'reload'], stdout=subprocess.DEVNULL)

    # Prepare final result
    print('[i] Done - Please see your installed regexps below\n')
    with open(path_legacy_regex, 'r') as fOpen:
        for line in fOpen:
            print(line, end='')
