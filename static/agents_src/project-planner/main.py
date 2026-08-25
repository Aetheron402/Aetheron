from pathlib import Path
import signal
import sys

from app import ProjectOrganizerApp


def main() -> None:
    root_dir = Path(__file__).parent
    config_path = root_dir / "config" / "default.json"

    app = ProjectOrganizerApp(config_path=config_path)

    def shutdown_handler(signum, frame):
        app.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    app.start()


if __name__ == "__main__":
    main()