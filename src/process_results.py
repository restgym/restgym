import common
import os
import sys
import sqlite3
import json
import re
import concurrent.futures
import multiprocessing
import math
import datetime
import csv
from rich.progress import Progress
from rich import print


MULTITHREADING = True
DEFAULT_JACCARD_SIMILARITY_THRESHOLD = 0.7
JACCARD_SIMILARITY_THRESHOLDS = {
    'features-service': 0.8,        # Manually confirmed
    'languagetool': 0.8,            # Manually confirmed
    'person-controller': 0.9,       # Manually confirmed (Very long messages with many tokens in common)
    'scs': 0.7,                     # No 500 in this API
    'genome-nexus': 0.7,            # Manually confirmed
    'market': 0.7,                  # Manually confirmed
    'project-tracking-system': 0.9, # Manually confirmed (Just one generic error)
    'user-management': 0.7,         # Manually confirmed
    'ncs': 0.7,                     # No 500 in this API
    'restcountries': 0.7,           # Manually confirmed (Just one generic error)
    'newbee': 0.7,                  # Manually confirmed
    'blog': 0.7,                    # Manually confirmed (Error with identical text)
    'google-drive': 0.7,
    'erc20': 0.8,
    'flight-search': 0.7,
    'gestao-hospital': 0.7,
    'kafka-rest-proxy': 0.7,
    'notebook-manager': 0.7,
    'pet-clinic': 0.7,
}
JACCARD_SIMILARITY_THRESHOLD_FALLBACK = 0.7

# Collect paths of completed runs (those with completed.txt file)
def collect_completed_runs():
    completed_runs = set()
    api_dirs = os.scandir(f'{common.RESTGYM_BASE_DIR}/results')
    for api_dir in api_dirs:
        if api_dir.is_dir():
            tool_dirs = os.scandir(api_dir)
            for tool_dir in tool_dirs:
                if tool_dir.is_dir():
                    run_dirs = os.scandir(tool_dir)
                    for run_dir in run_dirs:
                        if os.path.exists(run_dir.path + '/completed.txt'):
                            completed_runs.add(run_dir.path)
    return completed_runs


# Collect paths of completed runs (those with completed.txt file)
def collect_verified_runs():
    verified_runs = set()
    api_dirs = os.scandir(f'{common.RESTGYM_BASE_DIR}/results')
    for api_dir in api_dirs:
        if api_dir.is_dir():
            tool_dirs = os.scandir(api_dir)
            for tool_dir in tool_dirs:
                if tool_dir.is_dir():
                    run_dirs = os.scandir(tool_dir)
                    for run_dir in run_dirs:
                        if os.path.exists(run_dir.path + '/verified.txt'):
                            verified_runs.add(run_dir.path)
    return verified_runs

# Collect paths of processed run (those with summary.json file)
def collect_processed_runs():
    processed_runs = set()
    api_dirs = os.scandir(f'{common.RESTGYM_BASE_DIR}/results')
    for api_dir in api_dirs:
        if api_dir.is_dir():
            tool_dirs = os.scandir(api_dir)
            for tool_dir in tool_dirs:
                if tool_dir.is_dir():
                    run_dirs = os.scandir(tool_dir)
                    for run_dir in run_dirs:
                        if os.path.exists(run_dir.path + '/summary.json'):
                            processed_runs.add(run_dir.path)
    return processed_runs

# Collect paths of summaries
def collect_summaries():
    summaries = set()
    api_dirs = os.scandir(f'{common.RESTGYM_BASE_DIR}/results')
    for api_dir in api_dirs:
        if api_dir.is_dir():
            tool_dirs = os.scandir(api_dir)
            for tool_dir in tool_dirs:
                if tool_dir.is_dir():
                    run_dirs = os.scandir(tool_dir)
                    for run_dir in run_dirs:
                        if os.path.exists(run_dir.path + '/summary.json'):
                            summaries.add(run_dir.path + '/summary.json')
    return summaries


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


# Find minimum number of requests for API
def extract_minimum_req_num():
    result = {}
    processed_runs = collect_processed_runs()
    for processed_run in processed_runs:
        api = processed_run.split('/')[-3]
        with open(f"{processed_run}/summary.json", 'r') as summary_file:
            req_num = json.load(summary_file)['interactions']['count']
            if api not in result or result[api] > req_num:
                result[api] = req_num
    return result

# Compute statistics on interactions
def compute_stats_on_interactions(conn: sqlite3.Connection):
    cursor = conn.cursor()
    interactions_stats = {}
    interactions_stats['count'] = cursor.execute('SELECT COUNT(1) FROM interactions').fetchone()[0]
    interactions_stats['2XX'] = cursor.execute('SELECT COUNT(1) FROM interactions WHERE response_status_code >= 200 AND response_status_code < 300').fetchone()[0]
    interactions_stats['4XX'] = cursor.execute('SELECT COUNT(1) FROM interactions WHERE response_status_code >= 400 AND response_status_code < 500').fetchone()[0]
    interactions_stats['5XX'] = cursor.execute('SELECT COUNT(1) FROM interactions WHERE response_status_code >= 500 AND response_status_code < 600').fetchone()[0]
    interactions_stats['401'] = cursor.execute('SELECT COUNT(1) FROM interactions WHERE response_status_code = 401').fetchone()[0]
    interactions_stats['403'] = cursor.execute('SELECT COUNT(1) FROM interactions WHERE response_status_code = 403').fetchone()[0]
    interactions_stats['covered_operations'] = cursor.execute('SELECT COUNT(DISTINCT operation_id) FROM interactions').fetchone()[0]
    interactions_stats['unique_5XX'] = cursor.execute('SELECT COUNT(DISTINCT error_bucket_id) FROM interactions').fetchone()[0]
    return interactions_stats

# Prepare database to add tables and columns for processed results, or clears previous results if already processed
def prepare_database(conn: sqlite3.Connection, count, total):

    cursor = conn.cursor()

    # Check if database has "interactions" table
    interactions_table = cursor.execute("SELECT COUNT(1) FROM sqlite_master WHERE type='table' AND name = 'interactions'").fetchone()
    if len(interactions_table) == 0:
        print(f" => [ERROR] ({count}/{total}) Missing interaction table.")
        return

    # Create index on response_status_code column
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_interaction_response_status_code ON interactions (response_status_code ASC)")

    # Create columns in "interactions" table to store results, if they do not exist. If they exist, empty them
    columns = ['operation_id', 'error_bucket_id']
    for column in columns:
        column_count = cursor.execute("SELECT COUNT(1) FROM pragma_table_info('interactions') WHERE name = ?", (column,)).fetchone()[0]
        if column_count < 1:
            cursor.execute(f"ALTER TABLE interactions ADD COLUMN {column} INTEGER")
        else:
            cursor.execute(f"UPDATE interactions SET {column} = NULL")

    # Delete "code_coverage" table if exists, as it was the result of previous analysis and it will be recomputed
    cursor.execute("DROP TABLE IF EXISTS code_coverage")

    # Create "code_coverage" table
    cursor.execute("CREATE TABLE code_coverage (id INTEGER PRIMARY KEY, sample_time TEXT, branch_coverage FLOAT, line_coverage FLOAT, method_coverage FLOAT)")

    # Delete "cumulative_results" tables if exists, as it was the result of previous analysis and they will be recomputed
    cursor.execute('DROP TABLE IF EXISTS cumulative_results')

    # Create "cumulative_results" table
    cursor.execute('CREATE TABLE IF NOT EXISTS cumulative_results (id INTEGER PRIMARY KEY, interaction_number INTEGER, request_time REAL, success_count INTEGER, client_error_count INTEGER, server_error_count INTEGER, operation_coverage INTEGER, unique_faults INTEGER, branch_coverage REAL, line_coverage REAL, method_coverage REAL)')

    # Commit changes
    conn.commit()

# Get API operations from specification
def get_operations(api):
    spec_path = f'{common.RESTGYM_BASE_DIR}/apis/{api}/specifications/{api}-openapi.json'
    operations = []
    id = 0
    methods = ['CONNECT', 'DELETE', 'GET', 'HEAD', 'OPTIONS', 'PATCH', 'POST', 'PUT', 'TRACE']
    with open(spec_path, 'r') as spec_file:
        paths = json.load(spec_file)['paths']
        for path in paths.keys():
            for method in methods:
                if method.lower() in paths[path]:
                    operation = {
                        'id': id,
                        'method': method,
                        'path': path
                    }
                    operations.append(operation)
                    id += 1
    return operations

# Assign an operation ID to successful interactions
def extract_operation_id_from_interaction(path, conn: sqlite3.Connection, count, total):

    api = path.split('/')[-3]
    cursor = conn.cursor()

    # Get operations from specification and add regex
    operations = get_operations(api)
    for operation in operations:
        # Start from path
        regex = operation['path']
        # Replace all occurrences of {pathParameters} with [^/]*
        while regex.find('{') > 0:
            start = regex.find('{')
            end = regex.find('}') + 1
            regex = regex.replace(regex[start:end], '[^/]*')
        # Replace slashes with escaped slashes
        regex = regex.replace('/', r'/')
        # Add an optional ending slash
        if not regex.endswith('/'):
            regex += r'/'
        regex += '?'
        operation['regex'] = regex

    # Collect interactions from database
    interactions = cursor.execute('SELECT id, request_method, request_path FROM interactions WHERE response_status_code >= 200 AND response_status_code < 300').fetchall()

    # Limit to one log alert message for each API
    already_alerted = False

    # Process interactions
    for interaction in interactions:
        interaction_id = interaction[0]
        interaction_method = interaction[1]
        if interaction_method == 'HEAD':
            interaction_method = 'GET'
        interaction_path = interaction[2]
        # Remove query parameters
        interaction_path = interaction_path.split('?')[0] # TODO: use urllib to parse the path more robustly
        # Consider double slashes same as slashes
        interaction_path = interaction_path.replace('//', '/')
        found_match = False
        for operation in operations:
            if interaction_method == operation['method']:
                match = re.search(operation['regex'], interaction_path)
                if match != None and (match.span()[1] == len(interaction_path) or (match.span()[1] < len(interaction_path) and interaction_path[match.span()[1]] == '?')):
                    found_match = True
                    operation_id = operation['id']
                    cursor.execute('UPDATE interactions SET operation_id = ? WHERE id = ?', (operation_id, interaction_id))
                    break
        if not found_match:
            if api != 'languagetool': # Added this to avoid false positives from languagetool
                if not already_alerted:
                    print(f" => [[yellow]-WARN[/yellow]] ({count}/{total}) NO_PATH_MATCH: Could not find a path match with {interaction_method} {interaction_path}.")
                    already_alerted = True
    conn.commit()

# Jaccard similarity
def jaccard_similarity(list1, list2):
    intersection = len(list(set(list1).intersection(list2)))
    union = (len(set(list1)) + len(set(list2))) - intersection
    return float(intersection) / union

# Preprocess response body content for bucketing
def preprocess_response_body(api, response_body):
    if api in ['languagetool']:
        # Remove common prefix
        response_body = response_body.split('\n')[0]
        response_body = response_body.removeprefix("Error: Internal Error: ")
        response_body = response_body.replace("(''' (code 39))", "( (code 39))")
        # Remove quoted text a non-alphabetic chars
        pattern_squote = "'[^']*'"
        pattern_dquote = '"[^"]*"'
        pattern_not_text = '[^a-zA-Z]+'
        response_body = re.sub(pattern_squote, ' ', response_body)
        response_body = re.sub(pattern_dquote, ' ', response_body)
        response_body = re.sub(pattern_not_text, ' ', response_body)
    elif api in ['features-service']:
        if '<body>' in response_body and '</body>' in response_body:
            response_body = response_body[response_body.find('<body>'):response_body.find('</body>')]
    elif api in ['market', 'user-management', 'blog', 'erc20', 'gestao-hospital']:
        try:
            message = (json.loads(response_body))['message']
            if api in ['market', 'erc20', 'gestao-hospital'] or len(message.strip()) > 4:
                response_body = message
        except:
            response_body = response_body
        if api == 'market':
            response_body = re.sub(r'\[.*\]', '', response_body)
    elif api in ['person-controller']:
        response_body = response_body.replace('"', ' ').replace(':', ' ').replace('{', ' ').replace('}', ' ').replace('[', ' ').replace(']', ' ').replace(',', ' ')
    elif api in ['pet-clinic']:
        try:
            json_content = json.loads(response_body)
            message = json_content['title'] + ' ' + json_content['detail']
            if api == 'market' or len(message.strip()) > 4:
                response_body = message
        except:
            response_body = response_body
    return response_body

# Bucket unique 5XX
def bucket_unique_5xx(path, conn: sqlite3.Connection, count, total):

    api = path.split('/')[-3]

    cursor = conn.cursor()
    interactions = cursor.execute('SELECT id, response_content FROM interactions WHERE response_status_code >= 500').fetchall()

    bucket_count = 0
    buckets = []

    for interaction in interactions:
        id = interaction[0]
        response_body = preprocess_response_body(api, interaction[1])
        words = response_body.split()
        if len(words) == 0:
            words = ['500']

        candidate_bucket = None
        candidate_similarity = 0
        for bucket in buckets:
            similarity = jaccard_similarity(words, bucket['words'])

            jaccard_similarity_threshold = JACCARD_SIMILARITY_THRESHOLDS.get(api, JACCARD_SIMILARITY_THRESHOLD_FALLBACK)

            if similarity >= jaccard_similarity_threshold and similarity > candidate_similarity:
                candidate_similarity = similarity
                candidate_bucket = bucket

        if candidate_bucket is None:
            candidate_bucket = {
                'words': words,
                'id': bucket_count
            }
            bucket_count += 1
            buckets.append(candidate_bucket)
        cursor.execute('UPDATE interactions SET error_bucket_id = ? WHERE id = ?', (candidate_bucket['id'], id))
    conn.commit()

# Compute code coverage on sample
def compute_code_coverage_on_sample(path_to_csv):
    code_coverage = {}
    total_branch = 0
    covered_branch = 0
    total_line = 0
    covered_line = 0
    total_method = 0
    covered_method = 0
    with open(path_to_csv) as f:
        lines = f.readlines()
        for line in lines:
            items = line.split(',')
            if '_COVERED' not in items[6] and '_MISSED' not in items[6]:
                covered_branch = covered_branch + int(items[6])
                total_branch = total_branch + int(items[6]) + int(items[5])
                covered_line = covered_line + int(items[8])
                total_line = total_line + int(items[8]) + int(items[7])
                covered_method = covered_method + int(items[12])
                total_method = total_method + int(items[12]) + int(items[11])
    code_coverage['branch'] = covered_branch / total_branch
    code_coverage['line'] = covered_line / total_line
    code_coverage['method'] = covered_method / total_method
    return code_coverage

# Compute code coverage on all samples
def extract_code_coverage(path, conn: sqlite3.Connection):
    cursor = conn.cursor()
    # Get coverage files
    files = os.listdir(path + common.CODE_COVERAGE_PATH)
    # Do not consider EXEC files, only CSV files
    for file in files:
        if file.endswith('.csv'):
            code_coverage = compute_code_coverage_on_sample(f'{path}/{common.CODE_COVERAGE_PATH}/{file}')
            time = file.removeprefix('jacoco_').removesuffix('.csv').replace('.', ':')
            cursor.execute('INSERT INTO code_coverage (sample_time, branch_coverage, line_coverage, method_coverage) VALUES (?, ?, ?, ?)', (time, code_coverage['branch'], code_coverage['line'], code_coverage['method']))
    conn.commit()

# Get final coverage
def get_final_coverage(conn: sqlite3.Connection):
    cursor = conn.cursor()
    final_coverage = {}
    code_coverage = cursor.execute("SELECT branch_coverage, line_coverage, method_coverage FROM code_coverage ORDER BY sample_time DESC LIMIT 1").fetchone()
    final_coverage['branch'] = code_coverage[0]
    final_coverage['line'] = code_coverage[1]
    final_coverage['method'] = code_coverage[2]
    return final_coverage

# Compute cumulative results and save it to table
def compute_cumulative_results(conn: sqlite3.Connection):

    SAMPLE_STEP = 100
    cursor = conn.cursor()

    i = SAMPLE_STEP
    upper_limit = cursor.execute('SELECT COUNT(1) FROM interactions').fetchone()[0]

    while i <= upper_limit:
        successes = cursor.execute('SELECT COUNT(1) FROM interactions WHERE response_status_code >= 200 AND response_status_code < 300 AND id <= ?', (i,)).fetchone()[0]
        client_failures = cursor.execute('SELECT COUNT(1) FROM interactions WHERE response_status_code >= 400 AND response_status_code < 500 AND id <= ?', (i,)).fetchone()[0]
        server_failures = cursor.execute('SELECT COUNT(1) FROM interactions WHERE response_status_code >= 500 AND response_status_code < 600 AND id <= ?', (i,)).fetchone()[0]
        operations_coverage = cursor.execute('SELECT COUNT(DISTINCT operation_id) FROM interactions WHERE operation_id NOT NULL AND id <= ?', (i,)).fetchone()[0]
        unique_faults = cursor.execute('SELECT COUNT(DISTINCT error_bucket_id) FROM interactions WHERE error_bucket_id NOT NULL AND id <= ?', (i,)).fetchone()[0]
        timestamps_of_interaction = cursor.execute('SELECT request_timestamp, response_timestamp FROM interactions WHERE id = ?', (i,)).fetchone()
        average_timestamp = round((timestamps_of_interaction[0] + timestamps_of_interaction[1]) / 2)
        time_of_ith_request = datetime.datetime.fromtimestamp(average_timestamp, datetime.timezone.utc)
        string_time_of_ith_request = time_of_ith_request.isoformat()

        row = cursor.execute('SELECT branch_coverage, line_coverage, method_coverage, ABS(strftime("%s", sample_time) - strftime("%s", ?)) AS time_distance FROM code_coverage ORDER BY time_distance LIMIT 1',
                             (string_time_of_ith_request,)).fetchone()

        branch_coverage = row[0]
        line_coverage = row[1]
        method_coverage = row[2]

        if row[3] > 5:
            print(" => [[yellow]-WARN[/yellow]] Code coverage sample too far away in time. ")

        cursor.execute('INSERT INTO cumulative_results (interaction_number, request_time, success_count, client_error_count, server_error_count, operation_coverage, unique_faults, branch_coverage, line_coverage, method_coverage) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (i, timestamps_of_interaction[0], successes, client_failures, server_failures, operations_coverage, unique_faults, branch_coverage, line_coverage, method_coverage))
        i += SAMPLE_STEP

    conn.commit()

# Process runs
def process_runs(paths):
    threads = math.floor(multiprocessing.cpu_count() * 0.9)
    count = 1
    total = len(paths)

    if total > 0:

        with Progress() as progress:
            analysis_task = progress.add_task("Analyzing...", total=total*8)

            with concurrent.futures.ThreadPoolExecutor(threads) as executor:
                for path in paths:
                    if MULTITHREADING:
                        executor.submit(process_run, path, count, total, progress, analysis_task)
                    else:
                        process_run(path, count, total, progress, analysis_task)
                    count += 1

    results_time = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')

    # (1) Aggregate results w.r.t. time budget
    summaries = collect_summaries()
    with open(f"{common.RESTGYM_BASE_DIR}/results/time_budget_aggregated_results_{results_time}.csv", mode='w') as aggregate_file:
        aggregate_writer = csv.writer(aggregate_file, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
        aggregate_writer.writerow(['api', 'tool', 'run', 'interactions', '2XX', '4XX', '5XX', 'covered_operations', 'unique_5XX', 'branch_coverage', 'line_coverage', 'method_coverage', 'area_2XX', 'area_4XX', 'area_5XX', 'area_covered_operations', 'area_unique_5XX', 'area_branch_coverage', 'area_line_coverage', 'area_method_coverage'])
        for summary in summaries:
            api_info = summary.split('/')
            conn = sqlite3.connect(str(summary).replace("summary.json", common.DB_FILENAME))
            cursor = conn.cursor()
            auc_query = """
                WITH pairs AS (SELECT LAG(request_time)       OVER (ORDER BY request_time) AS t1, request_time AS t2, success_count 
                                    , LAG(success_count)      OVER (ORDER BY request_time) AS success1, client_error_count
                                    , LAG(client_error_count) OVER (ORDER BY request_time) AS client1, server_error_count
                                    , LAG(server_error_count) OVER (ORDER BY request_time) AS server1, operation_coverage
                                    , LAG(operation_coverage) OVER (ORDER BY request_time) AS coverage1, unique_faults
                                    , LAG(unique_faults)      OVER (ORDER BY request_time) AS faults1, branch_coverage
                                    , LAG(branch_coverage)    OVER (ORDER BY request_time) AS branch1, line_coverage
                                    , LAG(line_coverage)      OVER (ORDER BY request_time) AS line1, method_coverage
                                    , LAG(method_coverage)    OVER (ORDER BY request_time) AS method1
                               FROM cumulative_results)
                SELECT SUM((success1 + success_count) / 2.0 * (t2 - t1))       AS success_auc,
                       SUM((client1 + client_error_count) / 2.0 * (t2 - t1))   AS client_error_auc,
                       SUM((server1 + server_error_count) / 2.0 * (t2 - t1))   AS server_error_auc,
                       SUM((coverage1 + operation_coverage) / 2.0 * (t2 - t1)) AS operation_coverage_auc,
                       SUM((faults1 + unique_faults) / 2.0 * (t2 - t1))        AS unique_faults_auc,
                       SUM((branch1 + branch_coverage) / 2.0 * (t2 - t1))      AS branch_coverage_auc,
                       SUM((line1 + line_coverage) / 2.0 * (t2 - t1))          AS line_coverage_auc,
                       SUM((method1 + method_coverage) / 2.0 * (t2 - t1))      AS method_coverage_auc
                FROM pairs
                WHERE t1 IS NOT NULL;
            """
            cursor.execute(auc_query)
            aucs = cursor.fetchone() or (0,) * 8
            with open(summary) as f:
                d = json.load(f)
                aggregate_writer.writerow([api_info[-4], api_info[-3], api_info[-2], d['interactions']['count'], d['interactions']['2XX'], d['interactions']['4XX'], d['interactions']['5XX'], d['interactions']['covered_operations'], d['interactions']['unique_5XX'], d['final_code_coverage']['branch'], d['final_code_coverage']['line'], d['final_code_coverage']['method'], aucs[0], aucs[1], aucs[2], aucs[3], aucs[4], aucs[5], aucs[6], aucs[7]])


    # (2) Aggregate results w.r.t. request budget
    minimums = extract_minimum_req_num()
    with open(f"{common.RESTGYM_BASE_DIR}/results/request_budget_aggregate_results_{results_time}.csv", mode='w') as aggregate_file:
        aggregate_writer = csv.writer(aggregate_file, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
        aggregate_writer.writerow(['api', 'tool', 'run', 'interactions', '2XX', '4XX', '5XX', 'covered_operations', 'unique_5XX', 'branch_coverage', 'line_coverage', 'method_coverage', 'area_2XX', 'area_4XX', 'area_5XX', 'area_covered_operations', 'area_unique_5XX', 'area_branch_coverage', 'area_line_coverage', 'area_method_coverage'])
        for processed_run in collect_processed_runs():
            api_info = processed_run.split('/')
            conn = sqlite3.connect(processed_run + '/' + common.DB_FILENAME)
            cursor = conn.cursor()
            result = cursor.execute("SELECT * FROM cumulative_results WHERE interaction_number = ?", (minimums[api_info[-3]],)).fetchone()
            if result is None:
                print(f" => [[yellow]-WARN[/yellow]]: {api_info[-2]} did not send enough requests to {api_info[-3]} in {api_info[-1]}. Using the maximum observed values.")
                result = cursor.execute("SELECT * FROM cumulative_results ORDER BY interaction_number DESC LIMIT 1").fetchone()
            area = cursor.execute('SELECT SUM(success_count), SUM(client_error_count), SUM(server_error_count), SUM(operation_coverage), SUM(unique_faults), SUM(branch_coverage), SUM(line_coverage), SUM(method_coverage) FROM cumulative_results WHERE interaction_number <= ?', (minimums[api_info[-3]],)).fetchone()
            aggregate_writer.writerow([api_info[-3], api_info[-2], api_info[-1], result[1], result[3], result[4], result[5], result[6], result[7], result[8], result[9], result[10], area[0]/minimums[api_info[-3]], area[1]/minimums[api_info[-3]], area[2]/minimums[api_info[-3]], area[3]/minimums[api_info[-3]], area[4]/minimums[api_info[-3]], area[5]/minimums[api_info[-3]], area[6]/minimums[api_info[-3]], area[7]/minimums[api_info[-3]]])

    print("[green]Aggregated results saved to CSV files.[/green]")

# Process a single run (for parallelization purposes)
def process_run(path, count, total, progress, task):

    print(f" => [[green]START[/green]] ({count}/{total}) Analyzing run: {'/'.join(os.path.normpath(path).split(os.sep)[-3:])}", flush=True)

    conn = sqlite3.connect(f"{path}/{common.DB_FILENAME}")

    # Prepare database to contain new info
    prepare_database(conn, count, total)
    progress.update(task, advance=1)

    # Infer API operation from interaction
    extract_operation_id_from_interaction(path, conn, count, total)
    progress.update(task, advance=1)

    # Bucket similar 5XX errors
    bucket_unique_5xx(path, conn, count, total)
    progress.update(task, advance=1)

    # Extract code coverage for all samples
    extract_code_coverage(path, conn)
    progress.update(task, advance=1)

    # Compute stats on interactions
    interactions_stats = compute_stats_on_interactions(conn)
    progress.update(task, advance=1)

    # Get final code coverage
    final_code_coverage = get_final_coverage(conn)
    progress.update(task, advance=1)

    # Compute cumulative results
    compute_cumulative_results(conn)
    progress.update(task, advance=1)

    # Compile summary
    summary = {
        'interactions': interactions_stats,
        'final_code_coverage': final_code_coverage
    }

    # Write to file
    with open(path+'/summary.json', 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=4)

    print(f" => [[green]-END-[/green]] ({count}/{total}) Analysis completed.")

    progress.update(task, advance=1)

# Main
if __name__ == "__main__":
    common.welcome()
    print("This is the data analysis module. It will process raw experiment data to extract cumulative results.")
    print("This module will only process runs that passed the verification.")
    verified_runs = collect_verified_runs()
    completed_but_not_verified_runs = collect_completed_runs().difference(verified_runs)
    processed_runs = collect_processed_runs()
    not_processed_runs = verified_runs.difference(processed_runs)

    if len(completed_but_not_verified_runs) > 0:
        print("[yellow]WARNING[/yellow]: Some runs are completed but not verified. This script will only analyze runs that passed the verification.")

    print(f"Found {len(verified_runs)} verified run, {len(processed_runs)} of which have already been analyzed ({len(not_processed_runs)} to analyze).")

    if len(verified_runs) == 0:
        print("No runs to process. Please execute the experiment, verify the executed runs, and the try again.")
        sys.exit(0)

    print(f"[1] (Re-)Analyze all completed and verified runs ({len(verified_runs)})")
    print(f"[2] Analyze only newly completed and verified runs ({len(not_processed_runs)})")

    choice = input("Your choice: ")
    if choice != '1' and choice != '2':
        print("Invalid choice!")
        sys.exit(1)
    elif choice == '1':
        process_runs(verified_runs)
    elif choice == '2':
        if len(not_processed_runs) == 0:
            print("All runs have already been processed. Regenerating aggregate results CSVs.")
        process_runs(not_processed_runs)