// Standalone dump of the NESQL "Item" table to a CSV file.
//
// Deliberately does SELECT * rather than picking specific columns, so we
// don't have to guess exact physical column names (Hibernate/Spring's
// naming strategy might turn "imageFilePath" into "image_file_path", or
// keep it as-is - rather than assume, we just dump everything with a
// real header row and sort it out from there).
//
// USAGE:
//   1. Find your hsqldb jar. Either:
//      - grab it fresh: https://repo1.maven.org/maven2/org/hsqldb/hsqldb/2.7.2/hsqldb-2.7.2-jdk8.jar
//      - or find it already cached from building nesql-exporter, under:
//        ~/.gradle/caches/modules-2/files-2.1/org.hsqldb/hsqldb/2.7.2/
//   2. Compile:
//        javac ExportItems.java
//   3. Run, pointing at your nesql-db files WITHOUT any extension, e.g.
//      if you have .minecraft/nesql/nesql-repository/nesql-db.properties,
//      pass .../nesql-repository/nesql-db (no ".properties"):
//        java -cp .:hsqldb-2.7.2-jdk8.jar ExportItems "/path/to/nesql-repository/nesql-db" items_export.csv
//      (On Windows, classpath separator is ";" not ":":
//        java -cp .;hsqldb-2.7.2-jdk8.jar ExportItems "C:\path\to\nesql-db" items_export.csv )
//
// This connects read-only so it won't touch/modify your export.

import java.io.FileWriter;
import java.io.PrintWriter;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.sql.Statement;

public class ExportItems {
    public static void main(String[] args) throws Exception {
        if (args.length < 2) {
            System.err.println("Usage: java ExportItems <path-to-nesql-db-without-extension> <output.csv>");
            System.exit(1);
        }
        String dbPath = args[0];
        String outPath = args[1];

        Class.forName("org.hsqldb.jdbc.JDBCDriver");
        String url = "jdbc:hsqldb:file:" + dbPath + ";readonly=true;shutdown=true";

        System.out.println("Connecting to: " + url);
        try (Connection conn = DriverManager.getConnection(url, "SA", "");
             Statement stmt = conn.createStatement();
             ResultSet rs = stmt.executeQuery("SELECT * FROM Item");
             PrintWriter out = new PrintWriter(new FileWriter(outPath))) {

            ResultSetMetaData meta = rs.getMetaData();
            int columns = meta.getColumnCount();

            // Header row - these are the REAL column names, whatever they
            // turned out to be.
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
                if (rowCount % 10000 == 0) {
                    System.out.println("...exported " + rowCount + " rows so far");
                }
            }
            System.out.println("Done. Exported " + rowCount + " rows to " + outPath);
        }
    }

    private static String csvEscape(String s) {
        // Collapse newlines (tooltips etc. can contain them) and quote
        // anything with a comma or quote in it.
        String cleaned = s.replace("\r", " ").replace("\n", " | ");
        if (cleaned.contains(",") || cleaned.contains("\"")) {
            return "\"" + cleaned.replace("\"", "\"\"") + "\"";
        }
        return cleaned;
    }
}
