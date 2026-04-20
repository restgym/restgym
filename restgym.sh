#!/bin/sh

# Constants
RESTGYM="RESTgym"
DOCKER_CMD="docker"
RESTGYM_IMAGE_NAME="restgym"
RESTGYM_BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
DOCKER_SOCKET_HOST="/var/run/docker.sock"
DOCKER_SOCKET_CONTAINER="/var/run/docker.sock"
DOCKER_BASE_COMMAND="$DOCKER_CMD run --rm --name restgym -it -v $DOCKER_SOCKET_HOST:$DOCKER_SOCKET_CONTAINER -v $RESTGYM_BASE_DIR/infrastructure:/app/infrastructure:ro -v $RESTGYM_BASE_DIR/apis:/app/apis:ro -v $RESTGYM_BASE_DIR/tools:/app/tools:ro -v $RESTGYM_BASE_DIR/results:/app/results -v $RESTGYM_BASE_DIR/restgym-config.yml:/app/restgym-config.yml:ro -e RESTGYM_BASE_DIR=$RESTGYM_BASE_DIR $RESTGYM_IMAGE_NAME"


# Check if Docker is installed, else exit
if ! command -v "$DOCKER_CMD" > /dev/null 2>&1; then
  echo "Docker is not installed. Please install and start Docker before using $RESTGYM."
  exit 2
fi


# Check if Docker is running, else exit
if "$DOCKER_CMD" info > /dev/null 2>&1; then
  :
else
  echo "Docker is installed but not running. Please start Docker before using $RESTGYM."
  exit 3
fi


# Check if the RESTgym Docker image is available in the system, else build it
if $DOCKER_CMD image inspect "$RESTGYM_IMAGE_NAME" > /dev/null 2>&1; then
  :
else
  echo "$RESTGYM Docker image not found in local image registry. Building... (Build occurs only once; subsequent runs will use the cached image.)"
  $DOCKER_CMD build --quiet -t "$RESTGYM_IMAGE_NAME" .
  if [ $? -eq 0 ]; then
    echo "$RESTGYM Docker build succeeded."
  else
    echo "$RESTGYM Docker build failed. This should not happen with a clean $RESTGYM repository. Try again or report the issue on GitHub."
  fi
fi




case "$1" in

  # Builds all the Docker images for APIs and tools
  build-images|b)
    NEW_NAME="restgym-image-builder"
    DOCKER_BASE_COMMAND=$(echo "$DOCKER_BASE_COMMAND" | sed "s|--name restgym|--name $NEW_NAME|")
    exec $DOCKER_BASE_COMMAND python3 src/build.py
    ;;


  # Launches the experiment
  launch-experiment|l)
    NEW_NAME="restgym-experiment-launcher"
    DOCKER_BASE_COMMAND=$(echo "$DOCKER_BASE_COMMAND" | sed "s|--name restgym|--name $NEW_NAME|")
    exec $DOCKER_BASE_COMMAND python3 src/run.py
    ;;


  # Checks for the integrity of raw data from experiments
  verify-data|v)
    NEW_NAME="restgym-data-verifier"
    DOCKER_BASE_COMMAND=$(echo "$DOCKER_BASE_COMMAND" | sed "s|--name restgym|--name $NEW_NAME|")
    exec $DOCKER_BASE_COMMAND python3 src/check.py
    ;;


  # Process raw data to extract structured results
  analyze-data|a)
    NEW_NAME="restgym-data-analyzer"
    DOCKER_BASE_COMMAND=$(echo "$DOCKER_BASE_COMMAND" | sed "s|--name restgym|--name $NEW_NAME|")
    exec $DOCKER_BASE_COMMAND python3 src/process_results.py
    ;;


  # Stops all RESTgym-related containers
  force-stop|s)
    # List container IDs whose names include "restgym"
    containers=$($DOCKER_CMD ps -qa --filter "name=restgym")
    if [ -z "$containers" ]; then
      echo "No $RESTGYM containers are running."
      exit 0
    fi
    # Stop all running containers (they will also be removes as they were run with the --rm argument)
    echo "Stopping all $RESTGYM containers."
    $DOCKER_CMD stop $containers    # Do not put double quotes around $containers. It will change the logic of the command.
    $DOCKER_CMD container rm $containers    # Do not put double quotes around $containers. It will change the logic of the command.
    echo "Done."
    ;;


  # Removes the RESTgym image from the local Docker image registry
  remove|r)
    echo "Removing $RESTGYM Docker image."
    $DOCKER_CMD rmi $RESTGYM_IMAGE_NAME
    echo "Removed."
  ;;


  # Print current version
  version)
    echo "$RESTGYM v2.0.1"
    ;;


  *)
    echo "Usage: $0 {build-images|b|launch-experiment|l|verify-data|v|analyze-data|a|force-stop|s|remove|r|version}" >&2
    exit 1
    ;;
esac