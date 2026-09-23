// Standalone dump of the NESQL "Fluid" table to a CSV file.
// Sibling to ExportItems.java - same approach, same reasoning (SELECT *
// with a real header row rather than guessing physical column names).
//
// USAGE:
//   1. Same hsqldb jar as before (org.hsqldb:hsqldb:2.7.2:jdk8).
//   2. Compile:
//        javac ExportFluids.java
//   3. Run, pointing at your nesql-db files WITHOUT any extension:
//        java -cp .:hsqldb-2.7.2-jdk8.jar ExportFluids "/path/to/nesql-repository/nesql-db" fluids_export.csv
//      (Windows: ";" instead of ":" as the classpath separator)
//
// Read-only connection, won't touch your export.

import java.io.FileWriter;
import java.io.PrintWriter;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.sql.Statement;

public class ExportFluids {
    public static void main(String[] args) throws Exception {
        if (args.length < 2) {
            System.err.println("Usage: java ExportFluids <path-to-nesql-db-without-extension> <output.csv>");
            System.exit(1);
        }
        String dbPath = args[0];
        String outPath = args[1];

        Class.forName("org.hsqldb.jdbc.JDBCDriver");
        String url = "jdbc:hsqldb:file:" + dbPath + ";readonly=true;shutdown=true";

        System.out.println("Connecting to: " + url);
        try (Connection conn = DriverManager.getConnection(url, "SA", "");
             Statement stmt = conn.createStatement();
             ResultSet rs = stmt.executeQuery("SELECT * FROM Fluid");
             PrintWriter out = new PrintWriter(new FileWriter(outPath))) {

            ResultSetMetaData meta = rs.getMetaData();
            int columns = meta.getColumnCount();

            StringBuilder header = new StringBuilder();
            for (int i = 1; i <= columns; i++) {
                if (i > 1) header.append(",");
                header.append(csvEscape(meta.getColumnLabel(i)));
            }
            out.println(header);
            System.out.println("Columns found: " + header);

            int rowCount = 0;
            while (rs.next()) {
                StringBuilder row = new StringBuilder();
                for (int i = 1; i <= columns; i++) {
                    if (i > 1) row.append(",");
                    Object value = rs.getObject(i);
                    row.append(csvEscape(value == null ? "" : value.toString()));
                }
                out.println(row);
                rowCount++;
            }
            System.out.println("Done. Exported " + rowCount + " rows to " + outPath);
        }
    }

    private static String csvEscape(String s) {
        String cleaned = s.replace("\r", " ").replace("\n", " | ");
        if (cleaned.contains(",") || cleaned.contains("\"")) {
            return "\"" + cleaned.replace("\"", "\"\"") + "\"";
        }
        return cleaned;
    }
}
