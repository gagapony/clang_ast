#include <iostream>
#include <vector>

void initialize() {
    std::cout << "Initializing system..." << std::endl;
}

void processData(int value) {
    std::vector<int> data;
    data.push_back(value);
    std::cout << "Processed value: " << value << std::endl;
}

void cleanup() {
    std::cout << "Cleaning up..." << std::endl;
}
