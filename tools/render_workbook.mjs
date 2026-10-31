// Read-only visual QA with the artifact runtime already installed on this computer.
import fs from 'node:fs/promises';
import { FileBlob, SpreadsheetFile } from '@oai/artifact-tool';

const [input, outputDir] = process.argv.slice(2);
await fs.mkdir(outputDir, { recursive: true });
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(input));
console.log((await workbook.inspect({kind:'sheet',include:'id,name',maxChars:3000})).ndjson);
const names = ['Project summary','Accepted totals','File issues','Review customers','Clean customers','Review orders','Clean orders','Review payments','Clean payments','Relationships'];
for (const name of names) {
  const preview = await workbook.render({sheetName:name,range:'A1:F12',scale:1.5,format:'png'});
  await fs.writeFile(`${outputDir}/${name.replaceAll(' ','-')}.png`,new Uint8Array(await preview.arrayBuffer()));
}
console.log('Rendered all ten demo workbook sheets.');
