import os, subprocess, sys

def run(cmd, shell, env=None):
    try:
        p = subprocess.Popen(
            cmd,
            shell=shell,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        out, err = p.communicate(timeout=5)
        print("OK", p.returncode, repr(out[:200]), repr(err[:200]))
    except Exception as e:
        print("ERR", type(e).__name__, repr(str(e)[:500]))

if __name__ == "__main__":
    # Posix
    env = dict(os.environ)
    env.pop("HOME", None)
    run(["ls", "-l"], shell=False, env=env)
    run("ls -l", shell=True, env=env)
