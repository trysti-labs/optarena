package main

import "gin-helpdesk/internal/api"

func main() {
	r := api.NewRouter()
	r.Run(":8080")
}
