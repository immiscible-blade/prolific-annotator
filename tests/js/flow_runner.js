// Reads {task, cases} JSON on stdin, prints the browser flow's walk() for each case.
const Flow = require(process.argv[2]);
let input = "";
process.stdin.on("data", (d) => (input += d));
process.stdin.on("end", () => {
  const { task, cases } = JSON.parse(input);
  process.stdout.write(JSON.stringify(cases.map((c) => Flow.walk(task, c))));
});
