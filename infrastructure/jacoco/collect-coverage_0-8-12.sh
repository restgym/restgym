sample_time=$(date +"%Y-%m-%dT%H.%M.%S")
exec_file=/results/$API/$TOOL/$RUN/code-coverage/jacoco_$sample_time.exec
csv_file=/results/$API/$TOOL/$RUN/code-coverage/jacoco_$sample_time.csv
java -jar /infrastructure/jacoco/org.jacoco.cli-0.8.12-nodeps.jar dump --address localhost --port 12345 --destfile $exec_file
class_files=$(find /api/classes/ -type f -name "*.class" | tr '\n' ' ' | sed 's/.\{1\}$//' | sed 's/\ /\ --classfiles\ /g')
java -jar /infrastructure/jacoco/org.jacoco.cli-0.8.12-nodeps.jar report $exec_file --classfiles $class_files --csv $csv_file