#include "calc.h"
#include "strutil.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    assert(calc_add(2, 3) == 5);
    assert(calc_multiply(2, 3) == 6);

    char greeting[] = "  hello  ";
    assert(strcmp(strutil_trim(greeting), "hello") == 0);

    printf("PASS\n");
    return 0;
}
