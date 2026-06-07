import common
import sqlite3
import os
import sys
import shutil
import re
import time
from rich import print
from rich.progress import Progress



# Collect paths of runs
def collect_runs():
    runs = set()
    api_dirs = os.scandir(f'{common.RESTGYM_BASE_DIR}/results')
    for api_dir in api_dirs:
        if api_dir.is_dir():
            tool_dirs = os.scandir(api_dir)
            for tool_dir in tool_dirs:
                if tool_dir.is_dir():
                    run_dirs = os.scandir(tool_dir)
                    for run_dir in run_dirs:
                        if run_dir.name != '.DS_Store':
                            runs.add(run_dir.path)
    return runs


# Read time budget from file
def parse_time_budget(file_path):
    with open(file_path, 'r') as f:
        content = f.read()

    # Match "Time budget: X minutes." where X is integer
    match = re.search(r'Time budget:\s*(\d+)', content)

    if match:
        minutes = int(match.group(1))
        if minutes > 0:
            return minutes
    return 60


# Count code coverage samples
def count_coverage_samples(run):
    files = os.listdir(f"{run}{common.CODE_COVERAGE_PATH}")
    exec_count = 0
    csv_count = 0
    for file in files:
        if file.endswith('.exec'):
            exec_count += 1
        elif file.endswith('.csv'):
            csv_count += 1
    return exec_count, csv_count


# Verify SQLite database integrity
def verify_database_integrity(run):
    conn = sqlite3.connect(f'{run}/{common.DB_FILENAME}')
    cursor = conn.cursor()
    cursor.execute("PRAGMA integrity_check;")
    result = cursor.fetchone()
    conn.close()
    if result[0] == "ok":
        return True
    else:
        return False


# Perform integrity analysis
def analyze():

    runs = collect_runs()
    print(f"Analyzing {len(runs)} runs.")

    analyzed = {}

    # Progress bar
    with Progress() as progress:

        verify_task = progress.add_task("Verifying run data...", total=len(runs)+1)
        progress.update(verify_task, advance=1)

        for run in runs:

            warnings = []
            errors = []

            # Get time budget for the run
            time_budget = parse_time_budget(f'{run}/time-budget.txt')

            # Verify started.txt exists
            if not os.path.exists(f'{run}/started.txt'):
                errors.append("The run did not start.")

            # Verify completed.txt exists
            if not os.path.exists(f'{run}/completed.txt'):
                errors.append("The run did not complete.")

            # Verify database exists
            if not os.path.exists(f'{run}/{common.DB_FILENAME}'):
                errors.append("The result database does not exist.")
            else:

                # Verify database integrity
                if not verify_database_integrity(run):
                    warnings.append("The database integrity check failed.")

                # Connect to the database
                conn = sqlite3.connect(f"{run}/{common.DB_FILENAME}")
                cursor = conn.cursor()

                # Verify the interaction table is the database
                if int(cursor.execute("SELECT COUNT(1) FROM sqlite_master WHERE type='table' AND name = 'interactions'").fetchone()[0]) == 0:
                    errors.append("The interaction table does not exist in the database.")
                else:

                    # Count interactions
                    interaction_count = int(cursor.execute('SELECT COUNT(1) FROM interactions').fetchone()[0])

                    # Check if any request was recoded
                    if interaction_count == 0:
                        errors.append("No requests recorded in the database.")
                    else:

                        # Verify at least 100 requests per minute (in average) have been sent
                        if interaction_count < 100 * time_budget:
                            warnings.append(f"Less than 100 requests per minute have been recorded: {interaction_count} requests in {time_budget} minutes.")

                        # Verify requests time span is at least 80% of the time budget
                        interaction_time_span = int(int(cursor.execute('SELECT MAX(request_timestamp) - MIN(request_timestamp) FROM interactions').fetchone()[0]) / 60)
                        if interaction_time_span < int(0.8 * time_budget):
                            warnings.append(f"Requests time span is of {interaction_time_span}/{time_budget} minutes.")

            # Verify code coverage dir exists
            if not os.path.exists(f"{run}{common.CODE_COVERAGE_PATH}"):
                errors.append("Code coverage directory does not exist.")
            else:

                # Verify all code coverage samples exist
                exec_count, csv_count = count_coverage_samples(run)
                if exec_count < 12 * time_budget:
                    errors.append("Missing code coverage EXEC samples.")
                if csv_count < 12 * time_budget:
                    errors.append("Missing code coverage CSV samples.")

            # Add result to returned dictionary
            analyzed[run] = {
                'errors': errors,
                'warnings': warnings
            }

            # Print if something is wrong
            if len(errors) + len(warnings) > 0:
                message = f" => Run {'/'.join(os.path.normpath(run).split(os.sep)[-3:])} => "
                if len(errors) > 0:
                    message += f"[red]ERRORS: {errors}[/red] "
                if len(warnings) > 0:
                    message += f"[yellow]WARNINGS: {warnings}[/yellow]"
                print(message)

            # If no errors, create verified.txt file no error have been encountered
            if len(errors) == 0:
                content = f"This run was verified at {time.ctime()}.\n"

                if len(warnings) > 0:
                    content += f"The verification passed with WARNINGS.\n\nWarnings: {warnings}\n"
                else:
                    content += "The verification PASSED!\n"

                with open(f'{run}/verified.txt', 'a') as f:
                    f.write(content)

            progress.update(verify_task, advance=1)

    return analyzed

# Removes runs with problems
def clean(runs):

    count = len(runs)

    for run in runs:
        shutil.rmtree(run, ignore_errors=True)
    print(f"Removed {count} runs.")

if __name__ == '__main__':
    common.welcome()
    print("This is the verify data module. It will check the integrity of experimental data of run.")
    input("Press ENTER to start or CTRL+C to cancel...")

    analyzed = analyze()

    with_errors = []
    with_warnings = []

    for run in analyzed:
        if len(analyzed[run]['errors']) > 0:
            with_errors.append(run)
        elif len(analyzed[run]['warnings']) > 0:
            with_warnings.append(run)

    if len(with_errors) + len(with_warnings) == 0:
        print(f"All {len(analyzed)} runs passed the verification.")

    else:
        to_delete = []

        print(f"Analyzed: {len(analyzed)}. [red]With errors: {len(with_errors)}.[/red] [yellow]With warnings: {len(with_warnings)}.[/yellow]")

        if input(f"Do you want to delete runs with ERRORS? (yes/no): ").strip().lower() == "yes":
            to_delete += with_errors
        if input(f"Do you want to delete runs with WARNINGS? (yes/no): ").strip().lower() == "yes":
            to_delete += with_warnings

        if len(to_delete) == 0:
            print("Nothing to delete.")
        else:
            clean(to_delete)