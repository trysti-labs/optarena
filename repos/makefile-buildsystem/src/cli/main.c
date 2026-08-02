#include <stdio.h>

#include "calc.h"
#include "strutil.h"

int main(int argc, char **argv) {
    (void)argv;
    if (argc > 1) {
        printf("unexpected arguments\n");
        return 1;
    }
    printf("add: %d\n", calc_add(2, 3));
    printf("multiply: %d\n", calc_multiply(2, 3));

    char greeting[] = "  hello  ";
    printf("trimmed: '%s'\n", strutil_trim(greeting));
    return 0;
}
