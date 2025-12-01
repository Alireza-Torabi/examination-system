import sys
import os

from app import create_app
from app.services.migrations import init_db, migrate_schema

app = create_app()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "initdb":
        with app.app_context():
            init_db()
    else:
        with app.app_context():
            migrate_schema()
        app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
