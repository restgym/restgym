import common
import socket
import random
import threading
import time
import psutil
import sys
import os
import yaml
from rich.progress import Progress
from rich import print


MINIMUM_CPUS = 4
MINIMUM_RAM_GB = 4
TIME_BUDGET_MINS = 60


specification_volumes = {}


# Read configuration form file to override default config
def read_config():
    # Override config from file, if available
    with open(f'{common.RESTGYM_BASE_DIR}/restgym-config.yml') as stream:
        try:
            config = yaml.safe_load(stream)
            global MINIMUM_CPUS, MINIMUM_RAM_GB, TIME_BUDGET_MINS
            MINIMUM_RAM_GB = int(config['minimum_ram_gb'])
            MINIMUM_CPUS = int(config['minimum_cpus'])
            TIME_BUDGET_MINS = int(config['time_budget_mins'])

        except yaml.YAMLError as exc:
            print("Could not parse RESTgym configuration file. Continuing with default configuration.")
            print(exc)



# Check if a TCP port is free
def check_tcp_port(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return not s.connect_ex(('localhost', port)) == 0


# Get random free TCP port
def get_random_free_tcp_port():
    remaining_attempts = 1000
    while remaining_attempts > 0:
        candidate_port = random.randrange(10_000, 60_000)
        if check_tcp_port(candidate_port):
            return candidate_port
        remaining_attempts -= 1
    print("[red]ERROR[/red]: could not find a free TCP port for the API.")
    sys.exit(1)


# Compute the remaining runs to execute
def compute_remaining_runs(desired_runs):
    remaining_runs = []
    apis = common.get_apis()
    print("Enabled APIs: ", apis)
    tools = common.get_tools()
    print("Enabled tools: ", tools)
    for api in apis:
        for tool in tools:
            try:
                base_dir = f"{common.RESTGYM_BASE_DIR}/results/{api}/{tool}"
                subdirs = os.listdir(base_dir)
                count = 0
                for subdir in subdirs:
                    if os.path.exists(f"{base_dir}/{subdir}/completed.txt"):
                        count += 1
                while count < desired_runs:
                    remaining_runs.append({'api': api, 'tool': tool})
                    count += 1
            except:
                for _ in range(desired_runs):
                    remaining_runs.append({'api': api, 'tool': tool})
    return remaining_runs


# Verify Docker images have been built
def check_docker_images(remaining_runs):
    images = set()
    missing_images = []
    for remaining_run in remaining_runs:
        images.add(remaining_run['tool'])
        images.add(remaining_run['api'])
    for image in images:
        try:
            common.DOCKER_CLIENT.images.get(common.DOCKER_PREFIX + image)
        except:
            missing_images.append(image)
    return missing_images


# Filter out runs with missing images
def filter_runs_with_missing_images(remaining_runs, missing_images):
    filtered_remaining_runs = []
    for remaining_run in remaining_runs:
        if remaining_run['tool'] not in missing_images and remaining_run['api'] not in missing_images:
            filtered_remaining_runs.append(remaining_run)
    return filtered_remaining_runs

# Remove excluded runs
def remove_matching_dicts(list1, list2):
    # Convert list2 dicts to hashable tuples (sorted for key order invariance)
    to_remove = {tuple(sorted(d.items())) for d in list2}

    # Filter list1 to exclude exact matches
    return [d for d in list1 if tuple(sorted(d.items())) not in to_remove]

# Check if there are enough resources for another concurrent run
def check_resources():
    available_ram = getattr(psutil.virtual_memory(), 'available')
    available_cpus = (1 - (psutil.cpu_percent() / 100)) * psutil.cpu_count()
    return available_ram > MINIMUM_RAM_GB * 1024 * 1024 * 1024 and available_cpus > MINIMUM_CPUS


# Deeper check of resources (10 checks each second)
def deep_check_resources():
    for _ in range(10):
        if not check_resources():
            return False
        time.sleep(1)
    return True


# Execute an experiment run
def launch_run(api, tool, run_count, total_runs, progress, experiment_task):
    attempts = 5
    successfully_completed = False
    progress.update(experiment_task, advance=1)

    while attempts > 0 and not successfully_completed:

        attempts -= 1
        error_occurred = False
        run = 'run-' + time.strftime('%Y%m%d-%H%M%S')
        results_path = f'{common.RESTGYM_BASE_DIR}/results/{api}/{tool}/{run}'
        ports = {'9090/tcp': get_random_free_tcp_port()}
        env = {
            'API': api,
            'TOOL': tool,
            'RUN': run,
            'TIME_BUDGET': TIME_BUDGET_MINS,
            'HOST': 'localhost'
        }

        os.makedirs(results_path, exist_ok=True, mode=0o777)
        os.makedirs(f'{results_path}{common.LOGS_PATH}', exist_ok=True, mode=0o777)

        message = '[green]START[/green]' if attempts == 4 else '[yellow]RETRY[/yellow]'

        with open(f'{results_path}/time-budget.txt', 'a') as f:
            f.write(f'Time budget: {TIME_BUDGET_MINS} minutes.\n')

        print(f" => [{message}] ({run_count}/{total_runs}) Running {tool} on {api} ({run}).")
        with open(f'{results_path}/started.txt', 'a') as f:
            f.write(f'Run started on {time.ctime()}.\n')

        api_container_name = f'{common.DOCKER_PREFIX}-{api}-for-{tool}--{run}'
        tool_container_name = f'{common.DOCKER_PREFIX}-{tool}-for-{api}--{run}'

        # Verify Docker images have been built
        try:
            common.DOCKER_CLIENT.images.get(common.DOCKER_PREFIX + remaining_run['api'])
            common.DOCKER_CLIENT.images.get(common.DOCKER_PREFIX + remaining_run['tool'])
        except:
            print(
                f" => [[red]ERROR[/red]] ({run_count}/{total_runs}) Execution failed for {remaining_run['tool']} on {remaining_run['api']}. Missing Docker image(s). Have you built them?")
            with open(f'{results_path}/errors.txt', 'a') as f:
                f.write(
                    f"Docker image(s) not found for API ({remaining_run['api']}) or tool ({remaining_run['tool']}).\n\n")
            error_occurred = True

        # Start API
        if not error_occurred:
            try:
                api_container = common.DOCKER_CLIENT.containers.run(
                    image=f'{common.DOCKER_PREFIX}{api}',
                    name=api_container_name,
                    environment=env,
                    ports=ports,
                    volumes=[f'{common.RESTGYM_BASE_DIR_HOST}/results/:/results/'],
                    mem_limit='16g',
                    nano_cpus=8_000_000_000,
                    user=f'{os.getuid()}:{os.getgid()}',
                    detach=True
                )

            except Exception as e:
                with open(f'{results_path}/errors.txt', 'a') as f:
                    f.write(f"Could not start API ({api}) container.\n{e}\n\n")
                error_occurred = True

        # Wait 45 seconds for the API to start
        if not error_occurred:
            time.sleep(45)
        else:
            time.sleep(2)

        # Get the host port assigned by docker
        if not error_occurred:
            try:
                api_container.reload()
                env['PORT'] = str(int(api_container.attrs['NetworkSettings']['Ports']['9090/tcp'][0]['HostPort']))
            except Exception as e:
                print(
                    f" => [[red]ERROR[/red]] ({run_count}/{total_runs}) Execution failed for {remaining_run['tool']} on {remaining_run['api']}. The port 9090 is not published. (API container has crashed or API/proxy are not exposed).")
                with open(f'{results_path}/errors.txt', 'a') as f:
                    f.write(
                        f"Could not start API ({api}) container. Docker did not publish port 9090 as expected. The API container crashed on startup or the API/proxy is not exposed.\n{e}\n\n")

        # Start tool
        if not error_occurred:
            try:
                tool_container = common.DOCKER_CLIENT.containers.run(
                    image=f'{common.DOCKER_PREFIX}{tool}',
                    name=tool_container_name,
                    environment=env,
                    privileged=True,
                    volumes=specification_volumes,
                    network_mode='host',
                    mem_limit='16gb',
                    nano_cpus=8_000_000_000,
                    detach=True
                )
                time.sleep(1)
            except Exception as e:
                api_container.stop()
                with open(f'{results_path}/errors.txt', 'a') as f:
                    f.write(f"Could not start tool ({tool}) container.\n{e}\n\n")
                error_occurred = True

        # Perform a health check of containers each minute, for 60 times
        if not error_occurred:
            for minute in range(1, TIME_BUDGET_MINS + 1):
                time.sleep(60)
                progress.update(experiment_task, advance=1)
                try:
                    api_container.reload()
                    if api_container.status == 'exited':
                        raise Exception("API container exited.")
                # If the container was removed or it exited
                except Exception as e:
                    print(f" => [[red]ERROR[/red]] ({run_count}/{total_runs}) API container stopped.")
                    with open(f'{results_path}/errors.txt', 'a') as f:
                        f.write(f"API container not running at minute {minute}. Aborting.\n{e}\n\n")
                    try:
                        tool_container.stop()
                    except:
                        pass
                    error_occurred = True
                    break
                try:
                    tool_container.reload()
                    if tool_container.status == 'exited':
                        raise Exception("Container exited")
                # If the container was removed or it exited
                except Exception as e:
                    print(f" => [[red]ERROR[/red]] ({run_count}/{total_runs}) Tool container stopped.")
                    with open(f'{results_path}/errors.txt', 'a') as f:
                        f.write(f"Tool container not running at minute {minute}. Aborting.\n{e}\n\n")
                    try:
                        api_container.stop()
                    except:
                        pass
                    error_occurred = True
                    break

        # Stop tool container
        if not error_occurred:
            try:
                tool_container.stop()
                tool_container.wait()
            except Exception as e:
                error_occurred = True
                with open(f'{results_path}/errors.txt', 'a') as f:
                    f.write(f'Could not stop tool ({tool}) container. It possibly crashed.\n{e}\n\n')
                try:
                    api_container.stop()
                except:
                    pass

        # Save log of tool container and remove it
        try:
            with open(f'{results_path}{common.LOGS_PATH}/{tool}-stdout.log', 'wb') as f_out, open(f'{results_path}{common.LOGS_PATH}/{tool}-stderr.log', 'wb') as f_err:
                f_out.write(tool_container.logs(stdout=True, stderr=False))
                f_err.write(tool_container.logs(stdout=False, stderr=True))
            tool_container.remove()
        except Exception as e:
            with open(f'{results_path}/errors.txt', 'a') as f:
                f.write(f'Could not write log or remove tool ({tool}) container.\n{e}\n\n')

        # Wait 5 seconds to let the API container store the database
        if not error_occurred:
            time.sleep(5)

        # Stop API container
        if not error_occurred:
            try:
                api_container.stop()
                api_container.wait()
            except Exception as e:
                error_occurred = True
                with open(f'{results_path}/errors.txt', 'a') as f:
                    f.write(f'Could not stop API ({api}) container. It possibly crashed.\n{e}\n\n')

        # Save log of API container and remove it
        try:
            with open(f'{results_path}{common.LOGS_PATH}/{api}-stdout.log', 'wb') as f_out, open(
                    f'{results_path}{common.LOGS_PATH}/{api}-stderr.log', 'wb') as f_err:
                f_out.write(api_container.logs(stdout=True, stderr=False))
                f_err.write(api_container.logs(stdout=False, stderr=True))
            api_container.remove()
        except Exception as e:
            with open(f'{results_path}/errors.txt', 'a') as f:
                f.write(f'Could not write log or remove API ({api}) container. \n{e}\n\n')

        # Final stages
        if not error_occurred:
            successfully_completed = True
            with open(f'{results_path}/completed.txt', 'a') as f:
                f.write(f'Run completed on {time.ctime()}.\n')
            print(f" => [[green]-END-[/green]] ({run_count}/{total_runs}) Run of {tool} on {api} ({run}) completed.")
        else:
            time.sleep(2)
            if attempts == 0:
                print(f" => [[red]ERROR[/red]] ({run_count}/{total_runs}) Run of {tool} on {api} ({run}) terminated with errors.")


# Main
if __name__ == "__main__":
    common.welcome()
    read_config()
    print("This is the run module. It will run up to 20 repetitions of the experiment for each tool and API.")
    print(f"Time budget: {TIME_BUDGET_MINS} minutes. Minimum CPUs: {MINIMUM_CPUS}. Minimum RAM: {MINIMUM_RAM_GB} GB.")
    try:
        desired_runs = int(input("How many runs? [1-20]: "))
    except:
        print("Please specify an whole number.")
        sys.exit(1)
    if desired_runs < 1 or desired_runs > 20:
        print("Please specify a number in the range 1-20.")
        sys.exit(1)

    remaining_runs = compute_remaining_runs(desired_runs)

    # Do not execute the following configurations (e.g., a tool does not support an API, or it is known to crash)
    skip_runs = [
        {'api': 'pet-clinic', 'tool': 'schemathesis'},          # Crashes while parsing regex
        {'api': 'flight-search', 'tool': 'autoresttest'},       # Crashes after 5 minutes
        {'api': 'flight-search', 'tool': 'restest'},            # Crashes
        {'api': 'features-service', 'tool': 'evomaster'},       # API Crashes
        {'api': 'kafka-rest-proxy', 'tool': 'resttestgen'},     # API crashes
        {'api': 'erc20', 'tool': 'cats'}                        # Crashes after 1 minute
    ]
    remaining_runs = remove_matching_dicts(remaining_runs, skip_runs)

    # Uncomment next line to launch a manual subset of runs
    # remaining_runs = [{'api': 'market', 'tool': 'restler'}]

    missing_images = check_docker_images(remaining_runs)
    if len(missing_images) > 0:
        filtered_remaining_runs = filter_runs_with_missing_images(remaining_runs, missing_images)
        print(
            f"Some Docker images required for the experiment have not been built. Skipping experiment runs that involve these images. Only {len(filtered_remaining_runs)} out of {len(remaining_runs)} runs can be launched.")
        remaining_runs = filtered_remaining_runs
    else:
        print(f"Runs planned for execution: {len(remaining_runs)}.")

    total_runs = len(remaining_runs)
    run_count = 0

    for api in common.get_apis():
        if common.check_enabled('api', api):

            # JSON specification
            specification_volumes[f'{common.RESTGYM_BASE_DIR_HOST}/apis/{api}/specifications/{api}-openapi.json'] = {
                'bind': f'/specifications/{api}-openapi.json',
                'mode': 'ro'
            }

            # YAML specification
            specification_volumes[f'{common.RESTGYM_BASE_DIR_HOST}/apis/{api}/specifications/{api}.yaml'] = {
                'bind': f'/specifications/{api}.yaml',
                'mode': 'ro'
            }

    input("Press ENTER to start the execution of the experiment (or CTRL+C to cancel)...")

    with Progress() as progress:
        experiment_task = progress.add_task("Running experiment...", total=total_runs*(TIME_BUDGET_MINS+1))

        threads = []

        while len(remaining_runs) > 0:

            run_count += 1

            # Pick random run
            remaining_run = remaining_runs.pop(random.randrange(len(remaining_runs)))

            # Stop until resources are available
            notify_no_resources = True
            while not deep_check_resources():
                if notify_no_resources:
                    print(f" => [[blue]-WAIT[/blue]] ({run_count}/{total_runs}) Waiting for system resources to be released.")
                    notify_no_resources = False
                time.sleep(30)

            # Launch run in separate thread
            run_thread = threading.Thread(
                target=launch_run,
                args=(remaining_run['api'], remaining_run['tool'], run_count, total_runs, progress, experiment_task)
            )
            run_thread.start()
            threads.append(run_thread)

            # If not last run, wait 60 seconds before launching next
            if len(remaining_runs) > 0:
                time.sleep(60)

        # Wait for all the threads to complete
        for t in threads:
            t.join()

        print("Execution completed.")