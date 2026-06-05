// orders service. Reads DATABASE_URL and serves on :9090 => SERVICE that
// writes_to orders-db. The migrations/ dir is the schema-binding evidence.
package main

import (
	"log"
	"net/http"
	"os"
)

func main() {
	dbURL := os.Getenv("DATABASE_URL") // -> writes_to orders-db
	log.Printf("orders starting; db=%s", dbURL)
	http.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte("ok"))
	})
	log.Fatal(http.ListenAndServe(":9090", nil))
}
