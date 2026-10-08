Repair procedure:
1. Read the issue and restate the wrong behavior in one sentence.
2. Locate the code: grep for the function, dialect or keyword named in the issue; open only the relevant region (sed -n 'START,ENDp' file).
3. Reproduce: write a short Python snippet that shows the wrong output or exception before changing anything.
4. Make the smallest change that restores correct behavior. Prefer fixing inverted conditions, misplaced statements or wrong constants over rewriting logic.
5. Re-run the reproduction and one or two nearby cases of the same feature to confirm nothing else changed.
6. Submit only after the reproduction shows the corrected behavior.
