"""Execute the composite action with a deterministic clock and system boundary."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


SYSTEM_COMMAND = r'''#!/bin/bash
set -eu
source "$HOME/scenario.sh"
read -r now < "$HOME/clock"
case "${0##*/}" in
  date) printf '%s\n' "$now" ;;
  sleep)
    now=$((now + $1))
    printf '%s\n' "$now" > "$HOME/clock"
    [ "$now" -le 600 ] || exit 99
    if [ "$now" -eq "$marker_at" ]; then
      printf '%s\n' "$now" > "$HOME/.testbox-last-activity"
    fi ;;
  stat) read -r mtime < "$HOME/.testbox-last-activity"; printf '%s\n' "$mtime" ;;
  sudo)
    [ "$sshd_error" -eq 0 ] || exit 1
    for port in $ports; do printf 'port %s\n' "$port"; done ;;
  ss)
    [ "$ss_error" -eq 0 ] || exit 1
    if [ "$now" -le "$connected_until" ]; then
      if [[ "$*" != *sport* || " $* " == *"sport = :$connection_port "* ]]; then
        printf 'ESTAB 0 0 127.0.0.1:%s 127.0.0.2:%s\n' "$connection_port" "$peer_port"
      fi
    fi ;;
  curl)
    while [ "$1" != '-d' ]; do shift; done
    printf '%s\n' "$2" > "$HOME/events/$now.json" ;;
esac
'''


def action_script():
    action = (Path(__file__).resolve().parents[1] / "action.yml").read_text()
    # Bind only the absolute state directory into the isolated fixture. The
    # complete action body, including readiness and expiry, executes unchanged.
    script = textwrap.dedent(action.split("      run: |\n", 1)[1])
    return script.replace("STATE=/tmp/.testbox", 'STATE="$HOME/state"')


class ActivityTests(unittest.TestCase):
    def run_action(self, **scenario):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "clock").write_text("0\n")
            settings = {"connected_until": 0, "connection_port": 22, "peer_port": 54321,
                        "marker_at": -1, "sshd_error": 0, "ss_error": 0, **scenario}
            ports = settings.pop("ports", [22])
            shell_settings = [f"{key}={int(value)}" for key, value in settings.items()]
            shell_settings.append("ports='" + " ".join(str(port) for port in ports) + "'")
            (home / "scenario.sh").write_text("\n".join(shell_settings) + "\n")
            (home / "events").mkdir()
            state = home / "state"
            state.mkdir()
            values = {
                "testbox_id": "tbx_fixture", "installation_model_id": "1",
                "auth_token": "fixture-only", "idle_timeout": "1",
                "api_url": "https://example.invalid", "runner_host": "example.invalid",
                "runner_ssh_port": "64000", "working_directory": "/repo",
                "adopted_run_id": "1",
            }
            for name, value in values.items():
                (state / name).write_text(value)
            commands = home / "bin"
            commands.mkdir()
            for name in ("date", "sleep", "stat", "sudo", "ss", "curl"):
                script = commands / name
                script.write_text(SYSTEM_COMMAND)
                script.chmod(0o755)
            env = {**os.environ, "HOME": str(home), "JOB_STATUS": "success",
                   "PATH": str(commands) + os.pathsep + os.environ["PATH"]}
            result = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-o", "pipefail"],
                                    input=action_script(), env=env, capture_output=True, text=True, timeout=10)
            events = [{"at": int(file.stem), "payload": json.loads(file.read_text())}
                      for file in sorted((home / "events").glob("*.json"), key=lambda file: int(file.stem))]
            return result, events

    def test_active_local_connection_defers_expiry_and_preserves_forwarded_endpoint(self):
        for ports, connection_port in [([22], 22), ([2222], 2222), ([22, 2222], 2222)]:
            with self.subTest(ports=ports):
                result, events = self.run_action(ports=ports, connection_port=connection_port, connected_until=180)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual([event["payload"]["status"] for event in events], ["ready", "completed"])
                self.assertEqual(events[0]["payload"]["ssh_port"], "64000")
                self.assertEqual(events[1]["at"], 240)

    def test_unrelated_connection_does_not_extend_idle_lease(self):
        result, events = self.run_action(connection_port=443, peer_port=64000, connected_until=180)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events[-1]["at"], 60)

    def test_marker_extends_idle_lease_after_short_command(self):
        result, events = self.run_action(marker_at=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events[-1]["at"], 90)

    def test_idle_lease_still_completes_successfully(self):
        result, events = self.run_action()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events[-1]["payload"]["status"], "completed")
        self.assertEqual(events[-1]["at"], 60)

    def test_unknown_activity_fails_instead_of_reporting_idle_completion(self):
        for scenario in ({"sshd_error": True}, {"ports": []}, {"ss_error": True}):
            with self.subTest(scenario=scenario):
                result, events = self.run_action(**scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("completed", [event["payload"]["status"] for event in events])


if __name__ == "__main__":
    unittest.main()
