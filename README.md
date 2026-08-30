# run-testbox

Second half of the Blacksmith Testbox action pair. This action runs as the final step in your workflow, after all dependency installation and setup steps are complete. It reads state from `/tmp/.testbox/` written by `begin-testbox`, phones home to the Testbox API with a `ready` status, prints connection info, and keeps the runner alive with an idle timeout monitoring loop.

Pair this with [useblacksmith/begin-testbox](https://github.com/useblacksmith/begin-testbox), which runs right after checkout to discover configuration from the VM metadata service and signal the `hydrating` status.

## Usage

```yaml
steps:
  - uses: actions/checkout@v4

  - name: Begin Testbox
    uses: useblacksmith/begin-testbox@v2
    with:
      testbox_id: ${{ inputs.testbox_id }}

  # --- your setup steps here ---
  - uses: actions/setup-node@v4
  - run: npm ci
  # --- end setup ---

  - name: Run Testbox
    uses: useblacksmith/run-testbox@v2
```

## Inputs

This action takes no inputs. All configuration is read from the `/tmp/.testbox/` state directory written by `begin-testbox`.

## Idle Timeout

The action uses hybrid activity detection to determine when the runner is idle:

1. **Active SSH connections** — every 30 seconds, the loop checks established TCP connections to sshd's VM-local listener ports via `ss`. Listener ports come from `sudo -n sshd -T`; the advertised client port may be forwarded and remains unchanged in connection info. This catches interactive SSH sessions, long-running one-shot commands, and rsync transfers — anything that holds a connection open during a polling interval. Failure to inspect the listeners or sockets fails the action rather than declaring the runner idle.
2. **Marker file** — the Blacksmith CLI touches `~/.testbox-last-activity` on every `testbox run` invocation. This catches short-lived commands that complete between polling intervals.

Any signal from either source resets the idle timer. If no activity is detected for the configured idle timeout (default 10 minutes), the action exits cleanly and the GitHub Actions job completes normally, allowing the VM to be reclaimed. That job result describes the hosting lifecycle, not the result of a command submitted with `blacksmith testbox run`; use the command's own exit status and reports for validation.
