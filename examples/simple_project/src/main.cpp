#include <iostream>
#include "utils.h"

int main() {
    std::cout << "Starting program..." << std::endl;
    initialize();
    processData(42);
    cleanup();
    std::cout << "Program finished." << std::endl;
    return 0;
}
