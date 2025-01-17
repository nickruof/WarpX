import argparse
import json
import os
import re


class FullProfile(object):
    """
    Class for parsing, storing and writing the full profile data
    """
    def __init__(self, lines, stdout_path, write_dir=None):
        self.lines = lines
        self.stdout_path = stdout_path
        self.write_dir = write_dir

        if self.write_dir is None:
            self.write_dir = os.path.dirname(self.stdout_path)
            print("write dir", self.write_dir)

        self.profile_dicts = []

    def parse_full_profiling_output(self):
        print("Parsing profiling output...")

        self.function_profiles = []
        # The first 5 lines are not needed for parsing
        del self.lines[0:5]
        excl_count = self.parse_section_of_profiling_output("excl")
        del self.lines[0:excl_count + 5]
        incl_count = self.parse_section_of_profiling_output("incl")

        self.profile_dicts.append({
            "frame" : {"name" : "function_profiles"},
            "metrics" : {},
            "children" : self.function_profiles
        })

        total_count = excl_count + incl_count

        print(f"Finished parsing profile output! Parsed {total_count} profiling strings.")

    def parse_section_of_profiling_output(self, section):
        '''
        Parses either the exclusive or inclusive sections of the TinyProfiler output

        Parameters
        ----------

        section (string): The section to parse the profiling output,
            either 'excl' for exclusive or 'incl' for inclusive.

        Returns
        -------

        The number of lines that were parsed
        '''
        if section != "excl" and section != "incl":
            raise RuntimeError(f"'section' was not either 'excl' or 'incl', instead was {section}")

        count = 0

        for line in self.lines:
            if "------" in line:
                break
            # these lines are of the form "Function()  ncalls  min  avg  max  max_percent"
            # with more spaces than are here

            # replace the multiple spaces with a single space
            line = re.sub(" +", " ", line)
            values = line.split(" ")

            # work backwards through the values in order to capture function names with spaces.
            max_percent = float(values[-1].replace("%", ""))
            max_time = float(values[-2])
            avg = float(values[-3])
            min_time = float(values[-4])
            n_calls = int(values[-5])
            name = "_".join(values[0:-5])

            # find the corresponding exclusive time
            if section == "incl":
                profile_index = None
                found = False
                # This could be made more efficient, but there aren't
                # many functions listed in the tiny profiler so making this
                # more efficient would only make this script less readable
                for i in range(len(self.function_profiles)):
                    profile = self.function_profiles[i]
                    if profile["frame"]["name"] == name:
                        found = True
                        profile_index = i

                if found:
                    matching_n_calls = self.function_profiles[profile_index]["metrics"]["n_calls"]

                    if matching_n_calls != n_calls:
                        print(f"\nWARNING: mismatch of n_calls! {n_calls} {matching_n_calls} for {name}\n")

                    metrics = self.function_profiles[profile_index]["metrics"]

                    metrics["incl_min"] = min_time
                    metrics["incl_avg"] = avg
                    metrics["incl_max"] = max_time
                    metrics["incl_max_percent"] = max_percent

                    count = count + 1
                    self.function_profiles[profile_index]["metrics"] = metrics
                    continue

                else:
                    print(f"Did not find inclusive function match for exclusive function {name}, adding it...")

            dict = {
                "frame" : {"name" : name},
                "metrics" : {
                    "n_calls" : n_calls,
                    f"{section}_min" : min_time,
                    f"{section}_avg" : avg,
                    f"{section}_max" : max_time,
                    f"{section}_max_percent" : max_percent
                }
            }

            self.function_profiles.append(dict)
            count = count + 1

        return count

    def write_file(self):
        if not self.profile_dicts:
            raise RuntimeError("write_file called before profile data was parsed!")

        print("Writing profile data json file...")
        with open(os.path.join(self.write_dir, "profile_data.json"), "w") as data_file:
            data_file.write(json.dumps(self.profile_dicts, indent=4))

        print(f"Finished writing json file as '{self.write_dir}/profile_data.json'.")

def main(stdout_path, write_dir=None):
    print(f"Path to stdout file: {stdout_path}")

    output_file = open(stdout_path, "r")

    profile_lines = []
    reached_profile = False
    print("Parsing stdout file...")
    count = 0
    while True:
        line = output_file.readline()

        if not line:
            break

        if reached_profile:
            profile_lines.append(line.strip())
            count = count + 1
        elif "TinyProfiler total time across processes" in line:
            reached_profile = True
            profile_lines.append(line.strip())
            count = count + 1

    print(f"Finished parsing stdout file! Found {count} lines from TinyProfiler.")

    full_profile = FullProfile(profile_lines, stdout_path, write_dir)

    full_profile.parse_full_profiling_output()
    full_profile.write_file()

def entry():
    """Reads command line arguments and passes to main(), called as an entry point"""
    parser = argparse.ArgumentParser()
    parser.add_argument("stdout_path", type=str, metavar="stdout_path", help="The path to the stdout file")
    parser.add_argument(
        "write_dir", type=str, metavar="write_dir",
        default=None, nargs='?',
        help="The JSON file is written to the specified directory. The default is the current working directory."
    )
    args = vars(parser.parse_args())
    path = args["stdout_path"]
    write_dir = args["write_dir"]

    main(path, write_dir)


if __name__ == "__main__":
    entry()
