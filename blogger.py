"""Thin entry point so `python blogger.py <command>` keeps working after the move to a
package. The real CLI lives in linkedin_blogger/cli.py; `python -m linkedin_blogger` works
too."""

from linkedin_blogger.cli import main

if __name__ == "__main__":
    main()
